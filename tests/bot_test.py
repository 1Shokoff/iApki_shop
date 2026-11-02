"""Проверка хэндлеров бота без сети: python -m tests.bot_test

Апдейты скармливаются диспетчеру напрямую, а Bot подменён заглушкой,
которая запоминает вызовы API вместо запросов в Telegram.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone

TOKEN = "123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
BUYER = 777
SELLER = 999
SUPPLIER = 888

os.environ.update(
    {
        "BOT_TOKEN": TOKEN,
        "SELLER_IDS": str(SELLER),
        "SUPPLIER_IDS": str(SUPPLIER),
        "WEBAPP_URL": "https://example.com",
        "PAYMENT_MODE": "manual",
        "PAYMENT_DETAILS": "СБП: +7 900 000-00-00",
        "PRICE_RUB": "3000",
        "LEGAL_NAME": "Самозанятый Тестов Т. Т.",
        "LEGAL_INN": "123456789012",
        "CONTACT_EMAIL": "help@example.com",
        "LOG_LEVEL": "WARNING",
    }
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import fixtures  # noqa: E402

# Витрина собирается из сборок в папке — тестам нужна своя папка с каталогом.
os.environ["IPA_DIR"] = str(fixtures.catalog_folder())

from aiogram import Bot  # noqa: E402
from aiohttp.test_utils import TestServer  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery,
    EditMessageText,
    SendDocument,
    SendMessage,
    SendPhoto,
    SendVideo,
)
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    Document,
    FSInputFile,
    Message,
    PhotoSize,
    Update,
    User,
    Video,
)

from app import const, db, promocodes  # noqa: E402
from app.config import load_config  # noqa: E402
from app.main import build_dispatcher  # noqa: E402
from app.service import OrderService  # noqa: E402

failures: list[str] = []
counter = {"id": 0}


def check(name: str, condition: bool, extra: str = "") -> None:
    if condition:
        print("  ok   %s" % name)
    else:
        failures.append(name)
        print("  FAIL %s %s" % (name, extra))


class StubBot(Bot):
    """Перехватывает исходящие вызовы Telegram API."""

    def __init__(self) -> None:
        super().__init__(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.calls: list[object] = []
        self.reject_photos = False

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        if isinstance(method, SendDocument):
            return make_message(chat_id=method.chat_id, text=method.caption or "", from_bot=True)
        if isinstance(method, SendPhoto):
            if self.reject_photos:
                raise TelegramBadRequest(method=method, message="Bad Request: wrong file")
            return make_message(
                chat_id=method.chat_id,
                text=method.caption or "",
                from_bot=True,
                photo=[PhotoSize(file_id="photo-file-id", file_unique_id="u1", width=1254, height=1254)],
            )
        if isinstance(method, (SendMessage, EditMessageText)):
            return make_message(
                chat_id=getattr(method, "chat_id", BUYER),
                text=getattr(method, "text", ""),
                from_bot=True,
            )
        return True

    def texts_to(self, chat_id: int) -> list[str]:
        """Тексты сообщений и подписи к картинкам."""
        out = []
        for m in self.calls:
            if getattr(m, "chat_id", None) != chat_id:
                continue
            if isinstance(m, (SendMessage, EditMessageText)):
                out.append(m.text)
            elif isinstance(m, SendPhoto):
                out.append(m.caption or "")
        return out

    def photos_to(self, chat_id: int) -> list[SendPhoto]:
        return [m for m in self.calls if isinstance(m, SendPhoto) and m.chat_id == chat_id]

    def reset(self) -> None:
        self.calls.clear()


def buttons_to(bot: StubBot, chat_id: int) -> list[tuple[str, str | None, str | None]]:
    """Кнопки под сообщениями в чат: подпись, callback_data и текст для копирования."""
    out = []
    for m in bot.calls:
        if getattr(m, "chat_id", None) != chat_id:
            continue
        markup = getattr(m, "reply_markup", None)
        if not markup:
            continue
        for row in markup.inline_keyboard:
            for b in row:
                out.append((b.text, b.callback_data, b.copy_text.text if b.copy_text else None))
    return out


def make_message(chat_id: int, text: str, from_bot: bool = False, photo: list | None = None) -> Message:
    counter["id"] += 1
    if photo:
        return Message(
            message_id=counter["id"],
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            from_user=User(id=0, is_bot=True, first_name="Бот"),
            photo=photo,
            caption=text,
        )
    return Message(
        message_id=counter["id"],
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(
            id=0 if from_bot else chat_id,
            is_bot=from_bot,
            first_name="Бот" if from_bot else "Тест",
            username=None if from_bot else "user%d" % chat_id,
        ),
        text=text,
    )


def message_update(chat_id: int, text: str) -> Update:
    counter["id"] += 1
    return Update(update_id=counter["id"], message=make_message(chat_id, text))


def file_update(chat_id: int, *, photo: bool, name: str = "") -> Update:
    """Покупатель прислал чек: картинкой или файлом."""
    counter["id"] += 1
    common = dict(
        message_id=counter["id"],
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=chat_id, is_bot=False, first_name="Тест", username="user%d" % chat_id),
    )
    if photo:
        message = Message(
            photo=[PhotoSize(file_id="receipt-photo", file_unique_id="r1", width=1280, height=720)],
            **common,
        )
    else:
        message = Message(
            document=Document(file_id="receipt-doc", file_unique_id="r2", file_name=name),
            **common,
        )
    counter["id"] += 1
    return Update(update_id=counter["id"], message=message)


def video_update(chat_id: int) -> Update:
    """Покупатель прислал запись экрана — Telegram шлёт её видео, а не файлом."""
    counter["id"] += 1
    message = Message(
        message_id=counter["id"],
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=chat_id, is_bot=False, first_name="Тест", username="user%d" % chat_id),
        video=Video(file_id="receipt-video", file_unique_id="v1", width=720, height=1280, duration=12),
    )
    counter["id"] += 1
    return Update(update_id=counter["id"], message=message)


def callback_update(chat_id: int, data: str) -> Update:
    counter["id"] += 1
    return Update(
        update_id=counter["id"],
        callback_query=CallbackQuery(
            id="cb%d" % counter["id"],
            from_user=User(id=chat_id, is_bot=False, first_name="Тест", username="user%d" % chat_id),
            chat_instance="chat%d" % chat_id,
            data=data,
            message=make_message(chat_id, "предыдущее сообщение"),
        ),
    )


async def main() -> int:
    cfg = load_config()
    tmp = tempfile.mkdtemp()
    await db.init(os.path.join(tmp, "bot.sqlite3"))

    bot = StubBot()
    service = OrderService(bot, cfg)
    dp = build_dispatcher(cfg, service)

    used = dp.resolve_used_update_types()
    print("Диспетчер")
    check("подписка на message", "message" in used)
    check("подписка на callback_query", "callback_query" in used)
    check("подписка на pre_checkout_query", "pre_checkout_query" in used, str(used))

    print("\nПокупатель")
    await dp.feed_update(bot, message_update(BUYER, "/start"))
    check("/start отвечает витриной", any("iApki" in t for t in bot.texts_to(BUYER)))
    photos = bot.photos_to(BUYER)
    check(
        "приветствие — картинка с подписью и кнопками",
        len(photos) == 1 and isinstance(photos[0].photo, FSInputFile) and photos[0].reply_markup is not None,
    )
    check("приветствие не дублируется текстом", not any(isinstance(m, SendMessage) for m in bot.calls))

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/start"))
    photos = bot.photos_to(BUYER)
    check("повторный /start шлёт картинку по file_id", len(photos) == 1 and photos[0].photo == "photo-file-id")

    bot.reset()
    bot.reject_photos = True
    await dp.feed_update(bot, message_update(BUYER, "/start"))
    bot.reject_photos = False
    check(
        "картинку не приняли — приветствие уходит текстом",
        any(isinstance(m, SendMessage) and "iApki" in m.text for m in bot.calls),
    )

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/about"))
    about = "\n".join(bot.texts_to(BUYER))
    check("/about показывает реквизиты", "ИНН 123456789012" in about, about[:120])
    check("/about показывает контакты", "help@example.com" in about)
    check("/about перечисляет документы",
          "Публичная оферта" in about and "/docs/offer" in about, about[-200:])

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/help"))
    check(
        "из помощи есть переход в «О нас»",
        any(
            isinstance(m, SendMessage) and m.reply_markup
            and any("nav:about" == b.callback_data for row in m.reply_markup.inline_keyboard for b in row)
            for m in bot.calls
        ),
    )

    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "nav:buy_confirm"))
    order = await db.get_active_order(BUYER)
    check("заказ создан кнопкой", order is not None and order["status"] == const.NEW)
    payment_text = "\n".join(bot.texts_to(BUYER))
    check("реквизиты показаны", "Оплата заказа" in payment_text)
    check("сумма к оплате в реквизитах", "Сумма к оплате" in payment_text, payment_text[:160])
    check("продавцу пришло уведомление", any("Новый заказ" in t for t in bot.texts_to(SELLER)))
    check(
        "поставщику пришло уведомление одной строкой",
        bot.texts_to(SUPPLIER) == ["Заказ № %s (%d) от @user%d" % (order["code"], order["day_no"], BUYER)],
        str(bot.texts_to(SUPPLIER)),
    )
    check(
        "в уведомлении есть кнопка «Открыть заказ»",
        any(b[1] == "o:ucard:%d" % order["id"] for b in buttons_to(bot, SUPPLIER)),
        str(buttons_to(bot, SUPPLIER)),
    )

    print("\nОплата только с чеком")
    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "o:claim:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("«Я оплатил» без чека заказ не двигает", order["status"] == const.NEW)
    check("бот просит прислать чек", any("Пришлите чек об оплате" in t for t in bot.texts_to(BUYER)))
    check("продавца без чека не беспокоим", not bot.texts_to(SELLER))

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "Я перевёл деньги"))
    order = await db.get_order(order["id"])
    check("текст без чека заказ не двигает", order["status"] == const.NEW)
    check("бот напоминает про чек", any("пришлите чек" in t for t in bot.texts_to(BUYER)),
          str(bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, file_update(BUYER, photo=True))
    order = await db.get_order(order["id"])
    check("чек картинкой отправил заказ на проверку", order["status"] == const.PAYMENT_CHECK)
    check(
        "продавцу ушёл чек с кнопками проверки оплаты",
        any(
            isinstance(m, SendPhoto) and m.chat_id == SELLER
            and "оплатил и приложил чек" in (m.caption or "")
            and any(b.callback_data == "o:payok:%d" % order["id"]
                    for row in m.reply_markup.inline_keyboard for b in row)
            for m in bot.calls
        ),
    )
    check("покупателю сказали, что заказ на проверке",
          any("отправлен на проверку оплаты" in t for t in bot.texts_to(BUYER)))
    check("и что продавец может ответить не сразу",
          any("может ответить не сразу" in t for t in bot.texts_to(BUYER)), str(bot.texts_to(BUYER))[:300])

    bot.reset()
    await dp.feed_update(bot, file_update(BUYER, photo=False, name="chek.pdf"))
    check(
        "ещё один чек файлом ушёл продавцу",
        any(
            isinstance(m, SendDocument) and m.chat_id == SELLER
            and "chek.pdf" in (m.caption or "") and "ещё один чек" in (m.caption or "")
            for m in bot.calls
        ),
    )
    check("покупателю подтвердили второй чек", any("отправлен продавцу" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, video_update(BUYER))
    check(
        "чек видео (запись экрана) ушёл продавцу видео",
        any(isinstance(m, SendVideo) and m.chat_id == SELLER and m.video == "receipt-video" for m in bot.calls),
    )

    bot.reset()
    await dp.feed_update(bot, callback_update(SELLER, "o:payno:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("продавец отклонил — заказ снова ждёт чек", order["status"] == const.NEW)

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "Вот чек: https://bank.ru/receipt/42."))
    order = await db.get_order(order["id"])
    check("ссылка на чек тоже отправляет заказ на проверку", order["status"] == const.PAYMENT_CHECK)
    check("продавцу ушла ссылка на чек",
          any('href="https://bank.ru/receipt/42"' in t for t in bot.texts_to(SELLER)),
          str(bot.texts_to(SELLER))[:300])

    print("\nПродавец")
    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "o:payok:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("покупатель не подтверждает оплату сам", order["status"] == const.PAYMENT_CHECK)

    await dp.feed_update(bot, callback_update(SELLER, "o:payok:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("продавец подтвердил оплату", order["status"] == const.PAID)
    check("у покупателя запросили UDID", any("UDID" in t for t in bot.texts_to(BUYER)))

    print("\nUDID текстом в чат")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "12345"))
    order = await db.get_order(order["id"])
    check("короткий текст не принят за номер", order["device_udid"] is None)
    check("есть подсказка, из чего состоит UDID",
          any("латинские буквы A–F" in t for t in bot.texts_to(BUYER)), str(bot.texts_to(BUYER)))

    bot.reset()
    cyrillic = "2b6f0cc904d137be2e17 30235f5664094b831186".replace("e", "\u0435").replace("c", "\u0441")
    await dp.feed_update(bot, message_update(BUYER, cyrillic))
    order = await db.get_order(order["id"])
    check("UDID с кириллическими е и с принят латиницей",
          order["device_udid"] == "2b6f0cc904d137be2e1730235f5664094b831186"
          and order["status"] == const.UDID, str(order["device_udid"]))
    check("продавец получил UDID", any("Получен UDID" in t for t in bot.texts_to(SELLER)))
    check("у продавца кнопка копирует UDID",
          any(b[2] == order["device_udid"] for b in buttons_to(bot, SELLER)), str(buttons_to(bot, SELLER)))

    print("\nПоставщик")
    bot.reset()
    await dp.feed_update(bot, callback_update(SUPPLIER, "o:ucard:%d" % order["id"]))
    card = "\n".join(bot.texts_to(SUPPLIER))
    check("в карточке номер заказа", order["code"] in card, card[:160])
    check("в карточке статус исполнения", const.TITLES[const.UDID] in card, card[:160])
    check("в карточке покупатель", "@user%d" % BUYER in card)
    check("в карточке время создания", "Создан:" in card)
    check("в карточке UDID", order["device_udid"] in card)
    check("лишнего в карточке нет", "Сумма" not in card and "Заметка" not in card, card[:160])
    check(
        "кнопка копирует UDID",
        any(b[2] == order["device_udid"] for b in buttons_to(bot, SUPPLIER)),
        str(buttons_to(bot, SUPPLIER)),
    )

    bot.reset()
    await dp.feed_update(bot, message_update(SUPPLIER, "/udids"))
    check("/udids перечисляет заказы с UDID",
          any("Заказы с UDID" in t for t in bot.texts_to(SUPPLIER)), str(bot.texts_to(SUPPLIER)))
    check("из списка открывается карточка",
          any(b[1] == "o:ucard:%d" % order["id"] for b in buttons_to(bot, SUPPLIER)))
    check("в списке UDID виден покупатель",
          any(b[1] == "o:ucard:%d" % order["id"] and "@user%d" % BUYER in b[0] for b in buttons_to(bot, SUPPLIER)),
          str(buttons_to(bot, SUPPLIER)))

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/udids"))
    check("покупателю список UDID недоступен",
          not any("Заказы с UDID" in t for t in bot.texts_to(BUYER)), str(bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, message_update(SUPPLIER, "/orders all"))
    check("поставщику команды продавца недоступны",
          any("Не понял сообщение" in t for t in bot.texts_to(SUPPLIER)), str(bot.texts_to(SUPPLIER)))

    bot.reset()
    before = (await db.get_order(order["id"]))["status"]
    await dp.feed_update(bot, callback_update(SUPPLIER, "o:scancel:%d" % order["id"]))
    check("поставщик не отменяет заказ", (await db.get_order(order["id"]))["status"] == before)

    print("\nЗавершение заказа")
    bot.reset()
    await dp.feed_update(bot, callback_update(SELLER, "o:installed:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("кнопка продавца закрывает заказ", order["status"] == const.DONE)
    check("покупатель получил инструкцию",
          any("Инструкция по заказу" in t for t in bot.texts_to(BUYER)))
    check("в инструкции есть шаги",
          any("Введите свой UDID" in t for t in bot.texts_to(BUYER)))
    instruction = next((t for t in bot.texts_to(BUYER) if "Инструкция по заказу" in t), "")
    check("режим разработчика — первым, до ботов",
          0 < instruction.find("режим разработчика") < instruction.find("Понадобятся два бота"),
          instruction[:200])

    print("\nПереписка")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "А когда примерно будет готово?"))
    check("вопрос ушёл продавцу", any("когда примерно" in t for t in bot.texts_to(SELLER)))
    check("покупателю подтверждение", any("Отправлено продавцу" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, callback_update(SELLER, "o:chat:%d" % order["id"]))
    await dp.feed_update(bot, message_update(SELLER, "Сегодня вечером будет готово"))
    check("ответ продавца дошёл", any("Сегодня вечером" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/chat %s" % order["code"]))
    check("/chat показывает переписку",
          any("когда примерно" in t for t in bot.texts_to(SELLER)))
    await dp.feed_update(bot, message_update(SELLER, "/reply %s Ответ командой" % order["code"]))
    check("/reply доставлен", any("Ответ командой" in t for t in bot.texts_to(BUYER)))

    print("\nКоманды продавца")
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/orders all"))
    check("/orders работает", any("Заказы" in t or "заказы" in t for t in bot.texts_to(SELLER)))
    mine = [b[0] for b in buttons_to(bot, SELLER) if b[1] == "o:card:%d" % order["id"]]
    check("в /orders рядом с номером покупатель и статус",
          bool(mine) and order["code"] in mine[0] and "@user%d" % BUYER in mine[0]
          and const.TITLES[order["status"]] in mine[0], str(mine))
    await dp.feed_update(bot, message_update(SELLER, "/find %s" % order["code"]))
    check("/find находит заказ", any(order["code"] in t for t in bot.texts_to(SELLER)))
    await dp.feed_update(bot, message_update(SELLER, "/stats"))
    check("/stats — аналитика за всё время",
          any("Аналитика · Всё время" in t and "Продаж" in t for t in bot.texts_to(SELLER)), str(bot.texts_to(SELLER))[-300:])
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/stats 7d"))
    check("/stats 7d", any("Аналитика · 7 дней" in t for t in bot.texts_to(SELLER)))
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/stats вчера"))
    check("кривой период — подсказка", any("/stats today" in t for t in bot.texts_to(SELLER)), str(bot.texts_to(SELLER)))
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/stats 01.08.0202-27.10.2026"))
    check("опечатка в годе — просьба проверить год, а не отчёт за 18 веков",
          any("проверьте год" in t for t in bot.texts_to(SELLER)), str(bot.texts_to(SELLER)))
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/stats"))
    check("покупателю /stats не отвечает аналитикой", not any("Аналитика" in t for t in bot.texts_to(BUYER)))

    print("\nПромокоды")
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/promos"))
    check("/promos без промокодов — подсказка, где создать",
          any("Промокодов пока нет" in t and "Mini App" in t for t in bot.texts_to(SELLER)), str(bot.texts_to(SELLER)))
    promo = await promocodes.create({"code": "OSEN-20", "kind": "percent", "value": 20, "maxUses": 50}, cfg, "seller:%d" % SELLER)
    spare = await promocodes.create({"code": "SPARE-1", "kind": "rub", "value": 500}, cfg, "seller:%d" % SELLER)
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/promos"))
    labels = [b[0] for b in buttons_to(bot, SELLER)]
    check("/promos — список кнопками: код, скидка, статус",
          "OSEN-20 · 20% · Активен" in labels and "SPARE-1 · 500 ₽ · Активен" in labels, str(labels))
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/promo osen–20"))
    text = " ".join(bot.texts_to(SELLER))
    check("/promo КОД — карточка, код ищется без учёта регистра и тире",
          "<code>OSEN-20</code>" in text and "3 000 ₽ → <b>2 400 ₽</b>" in text and "0 из 50" in text, text[:300])
    buttons = buttons_to(bot, SELLER)
    check("под карточкой — скопировать, выключить, удалить",
          ("📋 Скопировать код", None, "OSEN-20") in buttons
          and any(b[1] == "pc:off:%d" % promo["id"] for b in buttons)
          and any(b[1] == "pc:del:%d" % promo["id"] for b in buttons), str(buttons))
    await dp.feed_update(bot, callback_update(SELLER, "pc:off:%d" % promo["id"]))
    check("кнопка выключает промокод", not (await db.get_promocode(promo["id"]))["enabled"])
    bot.reset()
    await dp.feed_update(bot, callback_update(SELLER, "pc:del:%d" % spare["id"]))
    check("удаление сначала переспрашивает",
          any(b[1] == "pc:delyes:%d" % spare["id"] for b in buttons_to(bot, SELLER))
          and await db.get_promocode(spare["id"]) is not None)
    await dp.feed_update(bot, callback_update(SELLER, "pc:delyes:%d" % spare["id"]))
    check("после подтверждения промокод удалён", await db.get_promocode(spare["id"]) is None)
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/promo NOPE"))
    check("неизвестный код — понятный ответ", any("Промокода «NOPE» нет" in t for t in bot.texts_to(SELLER)))
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/promos"))
    await dp.feed_update(bot, callback_update(BUYER, "pc:on:%d" % promo["id"]))
    check("покупателю промокоды не показываются", not any("OSEN-20" in t for t in bot.texts_to(BUYER)))
    check("и кнопкой продавца он промокод не включит", not (await db.get_promocode(promo["id"]))["enabled"])

    print("\nПромокод покупателя в боте")
    shopper = 4242
    await promocodes.create({"code": "BOT-15", "kind": "percent", "value": 15}, cfg, "seller:%d" % SELLER)
    bot.reset()
    await dp.feed_update(bot, callback_update(shopper, "nav:buy"))
    check("на оформлении сказано, где ввести промокод", any("промокод" in t for t in bot.texts_to(shopper)))
    await dp.feed_update(bot, callback_update(shopper, "nav:buy_confirm"))
    mine = await db.get_active_order(shopper)
    check("под реквизитами — «Ввести промокод»",
          any(b[1] == "o:promo:%d" % mine["id"] for b in buttons_to(bot, shopper)), str(buttons_to(bot, shopper)))
    bot.reset()
    await dp.feed_update(bot, callback_update(shopper, "o:promo:%d" % mine["id"]))
    check("бот просит прислать код", any("Пришлите промокод" in t for t in bot.texts_to(shopper)))
    await dp.feed_update(bot, message_update(shopper, "NET-TAKOGO"))
    check("неизвестный код — причина и кнопка попробовать ещё",
          any("Такого промокода нет" in t for t in bot.texts_to(shopper))
          and any(b[1] == "o:promo:%d" % mine["id"] for b in buttons_to(bot, shopper)), str(bot.texts_to(shopper)))
    bot.reset()
    await dp.feed_update(bot, callback_update(shopper, "o:promo:%d" % mine["id"]))
    await dp.feed_update(bot, message_update(shopper, "bot-15"))
    text = " ".join(bot.texts_to(shopper))
    check("код применён: новая сумма в реквизитах",
          "BOT-15 применён" in text and "<code>2 550 ₽</code>" in text, text[-400:])
    check("под оплатой — «Убрать промокод»",
          any(b[1] == "o:unpromo:%d" % mine["id"] for b in buttons_to(bot, shopper)))
    bot.reset()
    await dp.feed_update(bot, callback_update(shopper, "o:unpromo:%d" % mine["id"]))
    check("промокод убран — снова полная цена",
          any("Промокод убран" in t and "3 000 ₽" in t for t in bot.texts_to(shopper))
          and (await db.get_order(mine["id"]))["price_rub"] == 3000)
    bot.reset()
    await dp.feed_update(bot, callback_update(shopper, "o:promo:%d" % mine["id"]))
    await dp.feed_update(bot, message_update(shopper, "А когда будет установка?"))
    check("вопрос вместо кода уходит продавцу, а не в проверку промокода",
          any("А когда будет установка?" in t for t in bot.texts_to(SELLER))
          and not any("Такого промокода нет" in t for t in bot.texts_to(shopper)), str(bot.texts_to(shopper)))

    print("\nУведомления")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/notify"))
    check("/notify показывает настройки",
          any("Уведомления" in t for t in bot.texts_to(BUYER)))
    await dp.feed_update(bot, callback_update(BUYER, "nt:%s" % const.NOTIFY_STATUS))
    prefs = await db.get_notify_prefs(BUYER)
    check("кнопка выключает вид", prefs.get(const.NOTIFY_STATUS) is False)
    await dp.feed_update(bot, callback_update(BUYER, "nt:%s" % const.NOTIFY_STATUS))
    prefs = await db.get_notify_prefs(BUYER)
    check("повторное нажатие включает обратно", prefs.get(const.NOTIFY_STATUS) is True)

    print("\nЗакрытие выполненного заказа")
    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "o:cancel:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("покупатель закрыл заказ кнопкой в боте", bool(order["closed_at"]))
    check("у продавца заказ остался выполненным", order["status"] == const.DONE)
    check("покупателю написали, что заказ закрыт, а не отменён",
          any("закрыт" in t and "отменён" not in t for t in bot.texts_to(BUYER)), str(bot.texts_to(BUYER)))

    print("\nУдаление данных")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/forget"))
    check("данные удалены", await db.get_user(BUYER) is None)
    check("у покупателя заказов больше нет", await db.get_active_order(BUYER) is None)
    kept = await db._fetchall("SELECT * FROM orders WHERE user_id = ?", (db.ANON_USER_ID,))
    check("заказы обезличены, а не удалены", bool(kept) and all(o["device_udid"] is None for o in kept))
    check("покупателю сказали, что осталось в учёте",
          any("номер заказа, сумма и даты" in t for t in bot.texts_to(BUYER)), str(bot.texts_to(BUYER)))
    check("настройки уведомлений удалены", await db.get_notify_prefs(BUYER) == {})

    print("\nОплата через Platega")
    fake = fixtures.FakePlatega()
    pserver = TestServer(fake.app())
    await pserver.start_server()
    pcfg = replace(cfg, payment_mode="platega", platega_merchant_id="m", platega_secret="s",
                   platega_url=str(pserver.make_url("")).rstrip("/"))
    psvc = OrderService(bot, pcfg)
    psvc.bot_username = "iapki_bot"
    # Роутеры — одни на процесс, поэтому второй диспетчер не собрать: подменяем его данные.
    dp["cfg"], dp["service"] = pcfg, psvc
    payer = 5151
    try:
        bot.reset()
        await dp.feed_update(bot, callback_update(payer, "nav:buy"))
        check("на оформлении — «СБП, карта или SberPay»",
              any("СБП, карта или SberPay" in t for t in bot.texts_to(payer)), str(bot.texts_to(payer))[:300])
        await dp.feed_update(bot, callback_update(payer, "nav:buy_confirm"))
        order = await db.get_active_order(payer)
        check("экран оплаты: страница Platega, чек присылать не нужно",
              any("присылать чек не нужно" in t for t in bot.texts_to(payer)))
        check("под ним — «Оплатить»", any(b[1] == "o:pay:%d" % order["id"] for b in buttons_to(bot, payer)))
        bot.reset()
        await dp.feed_update(bot, callback_update(payer, "o:pay:%d" % order["id"]))
        urls = [
            btn.url for m in bot.calls if getattr(m, "chat_id", None) == payer and getattr(m, "reply_markup", None)
            for row in m.reply_markup.inline_keyboard for btn in row if btn.url
        ]
        check("ссылка на страницу оплаты — кнопкой", bool(urls) and urls[0].startswith("https://pay.platega.io/"), str(urls))
        bot.reset()
        await dp.feed_update(bot, callback_update(payer, "o:paycheck:%d" % order["id"]))
        answers = [m.text or "" for m in bot.calls if isinstance(m, AnswerCallbackQuery)]
        check("«проверить» до оплаты — «пока не поступила»", any("пока не поступила" in a for a in answers), str(answers))
        fake.statuses[urls[0].split("id=")[1]] = "CONFIRMED"
        bot.reset()
        await dp.feed_update(bot, callback_update(payer, "o:paycheck:%d" % order["id"]))
        check("после оплаты — «Оплата получена» и запрос UDID", any("Оплата получена" in t for t in bot.texts_to(payer)))
        check("заказ оплачен без продавца", (await db.get_order(order["id"]))["status"] == const.PAID)
    finally:
        dp["cfg"], dp["service"] = cfg, service
        await pserver.close()

    await db.close()
    await bot.session.close()

    print("")
    if failures:
        print("ПРОВАЛЕНО: %d — %s" % (len(failures), ", ".join(failures)))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
