"""Заглушка бота на время технических работ: python -m app.maintenance

Пока основной бот остановлен (./maintenance.sh on), она забирает апдейты
у Telegram и на любое сообщение или нажатие кнопки отвечает, что идут
технические работы. Сами сообщения не сохраняются: покупатель пришлёт их
снова, когда бот вернётся.

Запускать её можно только вместо основного бота, не рядом с ним: Telegram
отдаёт апдейты одному получателю, второй получает 409 Conflict.

О заказах и базе заглушка ничего не знает — только токен и прокси:
во время работ код бота, база и .env могут меняться как угодно.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

from .config import load_env_file

log = logging.getLogger(__name__)

TEXT = (
    "🛠 <b>Идут технические работы</b>\n\n"
    "Бот и приложение iApki скоро снова заработают. Ваши заказы и оплаты в сохранности.\n\n"
    "Если вы сейчас присылали UDID, чек или вопрос продавцу — отправьте их ещё раз, "
    "когда работы закончатся."
)

# Всплывающее окно под кнопкой: Telegram показывает не больше 200 знаков.
ALERT = "🛠 Идут технические работы. Попробуйте через несколько минут."

# Не чаще раза в минуту на чат: тот, кто пишет подряд, не получит стену одинаковых ответов.
REPEAT_AFTER = 60.0


def build_dispatcher(repeat_after: float = REPEAT_AFTER) -> Dispatcher:
    dp = Dispatcher()
    answered: dict[int, float] = {}

    # Только личные чаты: в группе заглушка не должна отвечать на каждую реплику.
    @dp.message(F.chat.type == "private")
    async def on_message(message: Message) -> None:
        moment = time.monotonic()
        last = answered.get(message.chat.id)
        if last is not None and moment - last < repeat_after:
            return
        answered[message.chat.id] = moment
        await message.answer(TEXT)

    @dp.callback_query()
    async def on_button(call: CallbackQuery) -> None:
        await call.answer(ALERT, show_alert=True)

    # Оплата звёздами: подтверждать её некому — отказываем, деньги не спишутся.
    @dp.pre_checkout_query()
    async def on_checkout(query: PreCheckoutQuery) -> None:
        await query.answer(ok=False, error_message=ALERT)

    return dp


def make_bot() -> Bot:
    token = (os.getenv("BOT_TOKEN") or "").strip()
    if ":" not in token:
        raise SystemExit("BOT_TOKEN не задан или выглядит некорректно. Заполните .env")
    proxy = (os.getenv("TELEGRAM_PROXY") or "").strip()
    # Хостинг режет api.telegram.org — заглушке нужен тот же прокси, что и боту.
    session = AiohttpSession(proxy=proxy) if proxy else None
    return Bot(token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def run() -> None:
    load_env_file()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    bot = make_bot()
    dp = build_dispatcher()
    log.info("Заглушка техработ запущена: бот отвечает, что идут технические работы")
    # Апдейты, пришедшие, пока бот перезапускался, тоже получают ответ — не сбрасываем их.
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
