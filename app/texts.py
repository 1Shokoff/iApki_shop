from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from . import catalog, const, instruction as instruction_mod, udid as udid_mod
from .config import Config

DONE_MARK = "✅"
CURRENT_MARK = "🔸"
WAIT_MARK = "▫️"


def money(rub: int) -> str:
    return "{:,} ₽".format(rub).replace(",", " ")


def price_html(cfg: Config) -> str:
    """Цена каталога: во время акции — зачёркнутая обычная и цена по акции."""
    if cfg.promo_active():
        return "<s>%s</s> <b>%s</b>" % (money(cfg.price_rub), money(cfg.promo_price_rub))
    return "<b>%s</b>" % money(cfg.current_price)


def promo_line(cfg: Config) -> str:
    """«🔥 −50% в честь открытия — акция до 30 ноября» или пусто, если акции нет."""
    if not cfg.promo_active():
        return ""
    tail = " — акция %s" % cfg.promo_until_text if cfg.promo_until else ""
    return "🔥 <b>%s</b>%s" % (e(cfg.promo_title), tail)


STATS_PERIODS_HINT = "Период: /stats today · 7d · 30d · month · all · 01.10-20.10"


def analytics_text(r: dict) -> str:
    """Сводка /stats для продавца — из analytics.report."""
    p, s = r["period"], r["sales"]
    lines = ["<b>Аналитика · %s</b>" % e(p["title"]), "<i>%s</i>" % e(p["datesText"]), ""]
    if s["count"]:
        lines.append("Продаж: <b>%d</b> на <b>%s</b>" % (s["count"], money(s["revenue"])))
        lines.append("Средний чек: %s" % money(s["average"]))
        lost = r["cancelledAfterPaid"]
        if lost["count"]:
            lines.append("Отменено после оплаты: %d на %s" % (lost["count"], money(lost["revenue"])))
            lines.append("Без отменённых: %d на %s" % (r["net"]["count"], money(r["net"]["revenue"])))
    else:
        lines.append("Продаж за период нет.")
    lines.append("")
    if r["created"]:
        lines.append("<b>Заказы за период по статусам</b> · оформлено %d" % r["created"])
        for st in r["statuses"]:
            lines.append("%s — %d (%d%%)" % (e(st["title"]), st["count"], st["percent"]))
        lines.append("Дошли до оплаты: %d%%" % r["conversion"])
    else:
        lines.append("Новых заказов за период нет.")
    lines.append("")
    lines.append(
        "<i>Продажа — заказ с подтверждённой оплатой, по дате подтверждения. "
        "Тестовые заказы не учитываются.</i>"
    )
    lines.append(STATS_PERIODS_HINT)
    return "\n".join(lines)


PROMO_HINT = "Создать промокод или поменять его условия — в Mini App: «Продавец» → «Промокоды»."

PROMO_ASK = (
    "🎟 Пришлите промокод одним сообщением.\n\n"
    "Он применится к заказу <b>%s</b>, и бот пришлёт сумму со скидкой."
)


def promocode_list(data: dict, titles: dict[str, str]) -> str:
    """Шапка /promos: сколько промокодов и в каких они статусах."""
    if not data["total"]:
        return "<b>Промокодов пока нет</b>\n\n" + PROMO_HINT
    counts = " · ".join(
        "%s %d" % (title, data["counts"][key]) for key, title in titles.items() if data["counts"].get(key)
    )
    return "<b>Промокоды</b> · %d\n%s\n\n%s" % (data["total"], e(counts), PROMO_HINT)


def promocode_card(p: dict) -> str:
    """Карточка промокода для продавца — из promocodes.details."""
    lines = ["🎟 <code>%s</code> · %s" % (e(p["code"]), e(p["statusTitle"])), "", "Скидка: <b>%s</b>" % e(p["valueText"])]
    for row in p["prices"]:
        if row["final"]:
            price = "%s → <b>%s</b>" % (e(row["priceText"]), e(row["finalText"]))
        else:
            price = "не сработает: скидка не меньше цены %s" % e(row["priceText"])
        lines.append("%s: %s" % (e(row["title"]), price))
    lines += [
        "Действует: %s" % e(p["periodText"]),
        "Применён: %s" % e(p["usesText"]),
        "На одного покупателя: %s" % e(p["perUserText"]),
    ]
    if p["note"]:
        lines.append("Заметка: %s" % e(p["note"]))
    lines.append("Создал: %s, %s" % (e(p["createdBy"]), when(p["createdAt"])))
    orders = p.get("orders") or []
    if orders:
        lines.append("\n<b>Заказы с промокодом</b>")
        for o in orders[:10]:
            lines.append("%s · %s · %s · %s" % (e(o["code"]), e(o["statusTitle"]), e(o["priceText"]), e(o["buyer"])))
        if len(orders) > 10:
            lines.append("…и ещё %d — в Mini App" % (len(orders) - 10))
    if not p["canDelete"]:
        lines.append("\n<i>Промокод уже применяли — удалить нельзя, только выключить.</i>")
    lines.append("\n<i>%s</i>" % e(PROMO_HINT))
    return "\n".join(lines)


def bot_description(cfg: Config) -> str:
    """Текст профиля бота до нажатия «Старт» (обычный текст, до 512 знаков)."""
    text = (
        "iApki — приложения, которых нет в App Store, снова на вашем iPhone.\n\n"
        "Весь каталог за %s: оплата, UDID, установка сертификата и пошаговая "
        "инструкция — всё в этом чате.\n\nНажмите «Старт», чтобы открыть витрину."
    ) % money(cfg.current_price)
    if cfg.promo_active():
        until = ", акция %s" % cfg.promo_until_text if cfg.promo_until else ""
        text = "🔥 %s: весь каталог за %s вместо %s%s.\n\n%s" % (
            cfg.promo_title, money(cfg.promo_price_rub), money(cfg.price_rub), until, text
        )
    return text[:512]


def bot_short_description(cfg: Config) -> str:
    """Короткое описание в профиле и при пересылке бота (до 120 знаков)."""
    if cfg.promo_active():
        until = ", %s" % cfg.promo_until_text if cfg.promo_until else ""
        text = "🔥 %s: весь каталог за %s вместо %s%s" % (
            cfg.promo_title, money(cfg.promo_price_rub), money(cfg.price_rub), until
        )
    else:
        text = "Приложения для iPhone вне App Store. Весь каталог за %s." % money(cfg.current_price)
    return text[:120]


def promo_applies(cfg: Config, price_rub: int) -> bool:
    """Заказ оформлен по цене акции — тогда к сумме дописываем, что скидка учтена."""
    return cfg.promo_active() and price_rub == cfg.promo_price_rub


def base_price(order: dict) -> int:
    """Цена заказа без промокода (с акцией, если она была)."""
    return order["price_rub"] + (order.get("promo_discount") or 0)


def promocode_line(order: dict) -> str:
    """«Промокод: OSEN-20 · −300 ₽ (без него 1 500 ₽)» для карточек; пусто — промокода не было."""
    if not order.get("promo_code"):
        return ""
    line = "Промокод: <code>%s</code> · −%s (без него %s)" % (
        e(order["promo_code"]), money(order.get("promo_discount") or 0), money(base_price(order))
    )
    if not order.get("promo_id"):
        line += " — применение возвращено"
    return line


def discount_note(order: dict, cfg: Config) -> str:
    """«Скидка уже учтена: …» под суммой к оплате — акция, промокод или оба."""
    parts = []
    if promo_applies(cfg, base_price(order)):
        parts.append(cfg.promo_title)
    if order.get("promo_id") and order.get("promo_code"):
        parts.append("промокод %s" % order["promo_code"])
    return "Скидка уже учтена: %s." % " и ".join(parts) if parts else ""


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(3, «файл», «файла», «файлов») → «3 файла»."""
    tail = n % 100
    last = n % 10
    if 11 <= tail <= 14 or last == 0 or last >= 5:
        word = many
    elif last == 1:
        word = one
    else:
        word = few
    return "%d %s" % (n, word)


def apps_count(n: int) -> str:
    """«1 приложение», «2 приложения», «24 приложения», «25 приложений»."""
    return plural(n, "приложение", "приложения", "приложений")


def e(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=False)


def when(iso: str) -> str:
    """Метки времени в базе в UTC — показываем во времени сервера."""
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return e(iso or "")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%d.%m %H:%M")


def user_line(order: dict) -> str:
    name = e(order.get("first_name") or "Покупатель")
    username = order.get("username")
    tail = " @" + e(username) if username else ""
    return "%s%s · <code>%s</code>" % (name, tail, order["user_id"])


# Заказ тестировщика из TEST_IDS — первой строкой во всём, что видят продавец и поставщик.
TEST_MARK = "🧪 <b>Тестовый заказ</b>"


def test_mark(order: dict) -> str:
    return TEST_MARK + "\n" if order.get("is_test") else ""


def staff_code(order: dict) -> str:
    """Номер заказа для продавца и поставщика: «NP-0155 (4)» — четвёртый за сутки.

    Покупателю номер за сутки не показываем: по нему видно, сколько у магазина
    заказов. Обычный текст, без HTML.
    """
    day_no = order.get("day_no")
    return "%s (%d)" % (order["code"], day_no) if day_no else order["code"]


def list_label(order: dict, tail: str) -> str:
    """Подпись кнопки в списке заказов: номер, покупатель и хвост (статус или время)."""
    return "%s%s · %s · %s" % ("🧪 " if order.get("is_test") else "", order["code"], buyer_label(order), tail)


def buyer_label(order: dict, limit: int = 20) -> str:
    """Покупатель для подписи кнопки — обычный текст, без HTML: @юзернейм, иначе имя."""
    username = order.get("username")
    if username:
        return "@" + username
    name = str(order.get("first_name") or order["user_id"]).strip()
    return name if len(name) <= limit else name[: limit - 1] + "…"


def user_ref(order: dict) -> str:
    """Покупатель одной строкой: @юзернейм, иначе имя, иначе Telegram ID."""
    username = order.get("username")
    if username:
        return "@" + e(username)
    return e(order.get("first_name") or order["user_id"])


def steps_block(order: dict) -> str:
    """Четыре шага воронки с отметками — как на экране «Мой заказ»."""
    step = const.STEP.get(order["status"], 0)
    labels = (
        "Оплата получена",
        "UDID передан",
        "Сертификат установлен",
        "Инструкция готова",
    )
    lines = []
    for i, label in enumerate(labels, start=1):
        if step > i:
            mark = DONE_MARK
        elif step == i:
            mark = CURRENT_MARK
        else:
            mark = WAIT_MARK
        lines.append("%s %s" % (mark, label))
    return "\n".join(lines)


HINTS = {
    const.NEW: "Оплатите заказ, чтобы продолжить.",
    const.PAYMENT_CHECK: (
        "Продавец проверяет оплату вручную и может ответить не сразу — например, "
        "если сейчас занят. Он постарается подтвердить платёж как можно быстрее, "
        "уведомление придёт в Telegram."
    ),
    const.PAID: "Пришлите UDID вашего iPhone.",
    const.UDID: "UDID у продавца. Идёт подготовка сертификата.",
    const.INSTALLED: "Сертификат готов, собираем инструкцию по установке.",
    const.DONE: "Заказ закрыт. Инструкция отправлена в этот чат.",
    const.CANCELLED: "Заказ отменён. Новый можно оформить командой /start.",
}


def buyer_order_card(order: dict) -> str:
    head = "<b>Заказ %s</b>\n%s · %s" % (
        e(order["code"]),
        e(catalog.product_name(order.get("app_slug"))),
        money(order["price_rub"]),
    )
    if order.get("promo_id") and order.get("promo_code"):
        head += "\nПромокод %s: −%s" % (e(order["promo_code"]), money(order.get("promo_discount") or 0))
    status = "Статус: <b>%s</b>" % e(const.TITLES.get(order["status"], order["status"]))
    body = steps_block(order)
    parts = [head, status, body]
    if order.get("device_udid"):
        parts.append("UDID: <code>%s</code>" % udid_mod.mask(order["device_udid"]))
    hint = HINTS.get(order["status"])
    if hint:
        parts.append("<i>%s</i>" % e(hint))
    return "\n\n".join(parts)


def seller_order_card(order: dict) -> str:
    lines = [
        "<b>Заказ %s</b> · %s" % (e(staff_code(order)), e(const.TITLES.get(order["status"], "—"))),
        "Покупатель: %s" % user_line(order),
        "Товар: %s" % e(catalog.product_name(order.get("app_slug"))),
        "Сумма: %s · оплата: %s" % (money(order["price_rub"]), e(order["payment_mode"])),
    ]
    if order.get("promo_code"):
        lines.append(promocode_line(order))
    if order.get("device_udid"):
        lines.append("UDID: <code>%s</code>" % e(order["device_udid"]))
    else:
        lines.append("UDID: —")
    if order.get("payment_ref"):
        lines.append("Платёж: <code>%s</code>" % e(order["payment_ref"]))
    if order.get("seller_note"):
        lines.append("Заметка: %s" % e(order["seller_note"]))
    lines.append("Создан: %s" % when(order["created_at"]))
    return "\n".join(lines)


def supplier_new_order(order: dict) -> str:
    """Уведомление поставщику о новом заказе — одной строкой."""
    return "Заказ № %s от %s" % (e(staff_code(order)), user_ref(order))


def supplier_order_card(order: dict) -> str:
    """Карточка поставщика: номер, покупатель, время создания и UDID — больше ему незачем."""
    lines = [
        "<b>Заказ № %s</b>" % e(staff_code(order)),
        "Статус: <b>%s</b>" % e(const.TITLES.get(order["status"], order["status"])),
        "Покупатель: %s" % user_ref(order),
        "Создан: %s" % when(order["created_at"]),
    ]
    if order.get("device_udid"):
        lines.append("UDID: <code>%s</code>" % e(order["device_udid"]))
    else:
        lines.append("UDID: покупатель ещё не присылал")
    return "\n".join(lines)


def welcome(first_name: str | None, cfg: Config, apps_count: int) -> str:
    name = e(first_name or "")
    hello = "Привет, %s!" % name if name else "Привет!"
    promo = promo_line(cfg)
    return (
        "%s\n\n"
        "%s"
        "<b>iApki — твои приложения снова на iPhone.</b>\n"
        "Весь каталог из %d приложений за %s. "
        "Без лимита установок и доплат за приложения.\n\n"
        "Как это работает:\n"
        "1. Оплата\n"
        "2. Вы присылаете UDID iPhone\n"
        "3. Мы ставим сертификат\n"
        "4. Вы получаете инструкцию\n"
    ) % (hello, promo + "\n\n" if promo else "", apps_count, price_html(cfg))


def payment_instructions(order: dict, cfg: Config) -> str:
    """Реквизиты перевода. Значения — в <code>: в Telegram они копируются нажатием."""
    lines = [
        "<b>Оплата заказа %s</b>" % e(order["code"]),
        "%s · <b>%s</b>" % (
            e(catalog.product_name(order.get("app_slug"))), money(order["price_rub"])
        ),
        "",
    ]

    rows = cfg.requisites
    if rows:
        for row in rows:
            value = "<code>%s</code>" % e(row["value"]) if row["copy"] else e(row["value"])
            lines.append("%s: %s" % (e(row["label"]), value))
    elif cfg.payment_details:
        lines.append(e(cfg.payment_details))
    else:
        lines.append("Реквизиты уточните у продавца.")

    # Сумму показываем всегда: она известна и без заполненных реквизитов.
    # С промокодом — только цена заказа: PAY_AMOUNT из .env о скидке не знает.
    amount = money(order["price_rub"]) if order.get("promo_id") else cfg.amount_text(order["price_rub"])
    lines.append("Сумма к оплате: <code>%s</code>" % e(amount))
    note = discount_note(order, cfg)
    if note:
        lines.append("<i>%s</i>" % e(note))

    if rows:
        lines.append("")
        lines.append("<i>Нажмите на значение, чтобы скопировать.</i>")
        if cfg.payment_details:
            lines.append("")
            lines.append(e(cfg.payment_details))

    lines.append("")
    lines.append(
        "Укажите в комментарии к платежу номер заказа <code>%s</code>. "
        "После перевода пришлите сюда <b>чек</b> — скриншот, PDF или ссылку на чек: "
        "без него заказ не уйдёт на проверку." % e(order["code"])
    )
    return "\n".join(lines)


def order_expire_soon(order: dict) -> str:
    """Напоминание за сутки до автоотмены неоплаченного заказа."""
    tail = (
        "Оплатить можно кнопкой ниже: СБП, карта или SberPay."
        if order.get("payment_mode") == "platega"
        else "Уже оплатили — пришлите сюда чек: скриншот, PDF или ссылку."
    )
    return (
        "⏳ Заказ <b>%s</b> ждёт оплаты — %s.\n"
        "Если оплаты не будет, через сутки он отменится автоматически.\n\n%s"
        % (e(order["code"]), money(order["price_rub"]), tail)
    )


# Способ оплаты словами — для оформления заказа в боте и витрине.
PAYMENT_MODE_TITLES = {
    "manual": "перевод с подтверждением",
    "demo": "демо, без списания",
    "stars": "Telegram Stars",
    "platega": "онлайн: СБП, карта или SberPay",
}


def platega_payment(order: dict, cfg: Config) -> str:
    """Экран оплаты в боте, когда платят через Platega: сумма и что будет дальше."""
    lines = [
        "<b>Оплата заказа %s</b>" % e(order["code"]),
        "%s · <b>%s</b>" % (e(catalog.product_name(order.get("app_slug"))), money(order["price_rub"])),
    ]
    note = discount_note(order, cfg)
    if note:
        lines.append("<i>%s</i>" % e(note))
    lines += [
        "",
        "Нажмите «Оплатить» — откроется страница Platega: СБП, банковская карта "
        "или SberPay на выбор. Чек пришлёт Platega.",
        "После оплаты бот сам пришлёт подтверждение — присылать чек не нужно.",
    ]
    return "\n".join(lines)


def platega_link(order: dict, link: dict) -> str:
    """Сообщение со ссылкой на оплату."""
    return (
        "💳 Ссылка на оплату заказа <b>%s</b> — %s.\n"
        "Она действует 15 минут. Оплатили — вернитесь в этот чат: подтверждение придёт само, "
        "обычно за минуту." % (e(order["code"]), money(link["amount"]))
    )


def order_expired(order: dict, days: int) -> str:
    """Сообщение покупателю: неоплаченный заказ отменён ботом."""
    return (
        "Заказ <b>%s</b> отменён: оплаты не было %s.\n"
        "Новый заказ можно оформить командой /start."
        % (e(order["code"]), plural(days, "день", "дня", "дней"))
    )


# Без чека оплата не заявляется: так продавцу не приходится искать платежи,
# которых не было.
RECEIPT_REQUIRED = (
    "Прикрепите чек об оплате — скриншот, PDF или ссылку. "
    "Без него заказ не уйдёт на проверку."
)

RECEIPT_PROMPT = (
    "<b>Пришлите чек об оплате</b>\n\n"
    "Скриншот перевода, PDF из банка или ссылку на чек — прямо сюда, в чат. "
    "Как только чек придёт, заказ уйдёт продавцу на проверку."
)


def instruction_message(order: dict, webapp_enabled: bool) -> str:
    """Инструкция в чат бота: шаги текстом и ссылки на ботов.

    Картинки шагов живут в витрине — сюда идёт только текст, иначе в чат
    прилетело бы десять тяжёлых слайдов.
    """
    lines = [
        "📄 <b>Инструкция по заказу %s</b>" % e(order["code"]),
        "",
    ]

    # Режим разработчика — до всего остального: без него приложение не запустится.
    developer = instruction_mod.DEVELOPER_MODE
    lines.append("⚙️ <b>%s</b>" % e(developer["title"]))
    for step in developer["steps"]:
        lines.append("• %s" % e(step))
    lines.append("<i>%s</i>" % e(developer["note"]))
    lines.append("")
    lines.append("Понадобятся два бота:")
    for bot in instruction_mod.BOTS:
        lines.append("• @%s — %s" % (e(bot["username"]), e(bot["hint"])))
    lines.append("")

    # Где взять сам .ipa — это первый вопрос после получения сертификата.
    sources = instruction_mod.sources()
    lines.append("📌 <b>%s</b>" % e(sources["title"]))
    lines.append(
        e(sources["text"]).replace(
            "@%s" % sources["channel"],
            '<a href="%s">@%s</a>' % (sources["url"], e(sources["channel"])),
        )
    )
    lines.append("")

    for number, step in enumerate(instruction_mod.STEPS, start=1):
        lines.append("<b>%d. %s</b>" % (number, e(step["title"])))
        lines.append(e(step["subtitle"]))
    lines.append("")

    udid = order.get("device_udid")
    if udid:
        lines.append("Ваш UDID: <code>%s</code>" % e(udid))
        lines.append("")

    if webapp_enabled:
        lines.append("<i>Инструкция со скриншотами каждого шага — в приложении, "
                     "на экране заказа.</i>")
    return "\n".join(lines).strip()


def receipt_caption(order: dict, header: str, title: str) -> str:
    """Подпись к чеку-файлу для продавца. Telegram режет подписи на 1024 знаках,
    поэтому только короткие поля — без заметок продавца."""
    # С промокодом сумма меньше обычной — продавцу сразу видно почему.
    promo = (" · промокод %s −%s" % (e(order["promo_code"]), money(order.get("promo_discount") or 0))
             if order.get("promo_code") else "")
    return "%s\n\n<b>Заказ %s</b> · %s\nПокупатель: %s\nСумма: %s%s\nЧек: %s" % (
        header,
        e(staff_code(order)),
        e(const.TITLES.get(order["status"], "—")),
        user_line(order),
        money(order["price_rub"]),
        promo,
        e(title[:200]),
    )


def receipt_part_caption(order: dict, index: int, total: int, title: str) -> str:
    """Подпись к одному из нескольких файлов чека: карточка с кнопками придёт следом."""
    return "🧾 Чек к заказу %s · файл %d из %d\n%s" % (e(staff_code(order)), index, total, e(title[:200]))


def receipt_summary(order: dict, header: str, count: int) -> str:
    """Карточка заказа после пачки файлов чека — с ней приходят кнопки продавца."""
    return "%s\n\n%s\n\nЧек — %s выше." % (
        header, seller_order_card(order), plural(count, "файл", "файла", "файлов")
    )


def receipt_link_message(order: dict, header: str, url: str) -> str:
    """Чек-ссылка для продавца: карточка заказа и ссылка, которую можно открыть."""
    shown = url if len(url) <= 80 else url[:77] + "…"
    return '%s\n\n%s\nЧек: <a href="%s">%s</a>' % (
        header, seller_order_card(order), escape(url, quote=True), e(shown)
    )


def udid_request(guide_url: str = "") -> str:
    lines = [
        "<b>Теперь добавьте iPhone</b>",
        "",
        "Нужен <b>UDID</b> — идентификатор устройства из цифр и латинских букв A–F. "
        "У новых iPhone он с дефисом: <code>00008150-000914312E78C01C</code>.",
        "",
        "<b>Как узнать UDID прямо на iPhone, без компьютера</b>",
        "1. Откройте в Safari сайт <b>udid.tech</b>",
        "2. Нажмите <b>Get My UDID</b> → <b>Разрешить</b> загрузку профиля",
        "3. Зайдите в <b>Настройки → Профиль загружен → Установить</b>. "
        "Если попросит — введите код-пароль iPhone",
        "4. После установки откроется страница с вашим UDID — скопируйте его",
        "",
        "Отправьте номер сообщением в этот чат.",
    ]
    if guide_url:
        lines.append("")
        lines.append('<a href="%s">Инструкция со скриншотами</a>' % e(guide_url))
    return "\n".join(lines)


def udid_accepted(order: dict) -> str:
    return (
        "UDID принят: <code>%s</code>\n\n"
        "Заказ %s передан продавцу. Следующий шаг — установка сертификата."
    ) % (udid_mod.mask(order["device_udid"]), e(order["code"]))


def help_text(support_username: str, has_webapp: bool) -> str:
    lines = [
        "<b>Помощь</b>",
        "",
        "/start — витрина и новый заказ",
        "/order — статус вашего заказа",
        "/udid — как найти UDID",
        "/about — документы, контакты и реквизиты",
        "/forget — удалить мои данные из бота",
    ]
    if has_webapp:
        lines.append("")
        lines.append("Кнопка «Открыть приложение» показывает каталог и статус заказа.")
    if support_username:
        lines.append("")
        lines.append("Живой человек: @%s" % e(support_username))
    return "\n".join(lines)


def doc_links(cfg: Config) -> str:
    """Строка со ссылками на документы; пустая, пока документов нет."""
    links = []
    for key, title, _hint in const.DOCUMENTS:
        url = cfg.doc_url(key)
        if url:
            links.append('<a href="%s">%s</a>' % (e(url), e(title)))
    return " · ".join(links)


def legal_footer(cfg: Config) -> str:
    """Подвал сообщения: реквизиты и документы — то же, что в витрине."""
    lines = []
    if cfg.legal_line:
        lines.append(e(cfg.legal_line))
    links = doc_links(cfg)
    if links:
        lines.append(links)
    if not lines:
        return ""
    return "\n\n<i>%s</i>" % "\n".join(lines)


CONSENT_NOTE = (
    "Оформляя заказ, вы принимаете условия публичной оферты и даёте согласие "
    "на обработку персональных данных."
)


def about(cfg: Config) -> str:
    """Экран «О нас»: что за сервис, документы, контакты и реквизиты.

    Те же сведения показывает Mini App. Платёжная система проверяет,
    что покупатель видит их до оплаты, поэтому не готовые пункты
    показываем честно, а не прячем.
    """
    lines = [
        "<b>О сервисе iApki</b>",
        "",
        "Ставим на ваш iPhone приложения, которых нет в App Store: "
        "в каталоге %s. Оплата → вы присылаете UDID → мы ставим "
        "сертификат → вы получаете инструкцию." % apps_count(catalog.count()),
        "",
        "<b>Документы</b>",
    ]
    for key, title, _hint in const.DOCUMENTS:
        url = cfg.doc_url(key)
        lines.append(
            '• <a href="%s">%s</a>' % (e(url), e(title)) if url
            else "• %s — готовится" % e(title)
        )

    contacts = []
    if cfg.contact_email:
        contacts.append("Почта: %s" % e(cfg.contact_email))
    if cfg.contact_phone:
        contacts.append("Телефон: %s" % e(cfg.contact_phone))
    if cfg.support_username:
        contacts.append("Telegram: @%s" % e(cfg.support_username))
    lines.append("")
    lines.append("<b>Контакты</b>")
    lines.extend(contacts or ["Появятся здесь до открытия продаж."])

    lines.append("")
    lines.append("<b>Реквизиты</b>")
    lines.append(e(cfg.legal_line) if cfg.legal_line else "Появятся здесь до открытия продаж.")
    return "\n".join(lines)


def chat_from_buyer(order: dict, body: str) -> str:
    return "💬 <b>Сообщение по заказу %s</b>\nОт: %s\n\n%s" % (
        e(staff_code(order)),
        user_line(order),
        e(body),
    )


def chat_from_seller(order: dict, body: str) -> str:
    return "💬 <b>Продавец по заказу %s</b>\n\n%s" % (e(order["code"]), e(body))


def chat_history(order: dict, messages: list[dict]) -> str:
    """Переписка по заказу — её открывает продавец (/chat, «💬 Ответить»)."""
    if not messages:
        return "По заказу <b>%s</b> переписки ещё нет." % e(staff_code(order))
    lines = ["<b>Переписка по заказу %s</b>" % e(staff_code(order)), ""]
    for msg in messages:
        who = "Продавец" if msg["author"] == "seller" else "Покупатель"
        lines.append(
            "<b>%s</b> · %s\n%s"
            % (who, when(msg["created_at"]), e(msg["text"]))
        )
    return "\n\n".join(lines)


def notify_screen(
    items: tuple[tuple[str, str, str], ...],
    prefs: dict[str, bool],
    order: dict | None,
    mode: str,
) -> str:
    lines = ["<b>Уведомления</b>", "Нажмите на пункт, чтобы включить или выключить.", ""]
    for key, title, hint in items:
        on = prefs.get(key, True)
        lines.append("%s <b>%s</b>\n<i>%s</i>" % ("🔔" if on else "🔕", title, e(hint)))
    if order:
        lines.append("")
        lines.append(
            "<b>Заказ %s:</b> %s\n<i>Настройка по заказу сильнее общей.</i>"
            % (e(order["code"]), e(const.ORDER_NOTIFY_TITLES[mode]))
        )
    lines.append("")
    lines.append(
        "<i>Статус заказа и инструкция всегда доступны в приложении, "
        "даже если уведомления выключены.</i>"
    )
    return "\n".join(lines)


CHAT_HINT_BUYER = (
    "Просто напишите сообщение в этот чат — продавец увидит его в карточке заказа "
    "и ответит здесь же."
)


PRIVACY = (
    "UDID нужен только для выпуска сертификата под ваше устройство. "
    "Он виден продавцу и хранится до закрытия заказа. "
    "Команда /forget удаляет ваши данные — имя, UDID и переписку; в учёте продаж "
    "остаются только номер заказа, сумма и даты."
)
