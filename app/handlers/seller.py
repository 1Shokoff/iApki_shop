from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import analytics, const, db, keyboards, promocodes, texts
from ..config import Config
from ..service import OrderService, ServiceError

log = logging.getLogger(__name__)
router = Router(name="seller")


class SellerFlow(StatesGroup):
    note = State()
    reply = State()


def setup(cfg: Config) -> Router:
    """Роутер работает только для аккаунтов из SELLER_IDS."""
    sellers = set(cfg.seller_ids)
    router.message.filter(F.from_user.id.in_(sellers))
    router.callback_query.filter(F.from_user.id.in_(sellers))
    return router


def _actor(call_or_msg) -> str:
    return "seller:%d" % call_or_msg.from_user.id


async def _card(order: dict) -> tuple[str, InlineKeyboardMarkup]:
    user = await db.get_user(order["user_id"]) or {}
    full = dict(order)
    full.setdefault("username", user.get("username"))
    full.setdefault("first_name", user.get("first_name"))
    return texts.test_mark(order) + texts.seller_order_card(full), keyboards.seller_order_kb(order)


# ------------------------------------------------------------------- команды


@router.message(Command("orders"))
async def cmd_orders(message: Message, command: CommandObject) -> None:
    arg = (command.args or "open").strip().lower()
    status = None if arg in ("all", "все") else (arg if arg in const.TITLES else "open")
    orders = await db.list_orders(status=status, limit=20)
    if not orders:
        await message.answer("Заказов нет.")
        return

    rows = [
        [
            InlineKeyboardButton(
                # По одному номеру не понять, чей заказ, — рядом покупатель.
                text=texts.list_label(o, const.TITLES.get(o["status"], o["status"])),
                callback_data=keyboards.cb("card", o["id"]),
            )
        ]
        for o in orders
    ]
    title = "Открытые заказы" if status == "open" else "Заказы: %s" % arg
    await message.answer(
        "<b>%s</b> · %d" % (title, len(orders)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, command: CommandObject) -> None:
    """Аналитика продаж: /stats, /stats 7d, /stats month, /stats 01.10-20.10."""
    try:
        period = analytics.period_from_text(command.args or "")
    except analytics.PeriodError as exc:
        await message.answer("%s\n\n%s" % (texts.e(str(exc)), texts.STATS_PERIODS_HINT))
        return
    await message.answer(texts.analytics_text(await analytics.report(period)))


# Кнопок в списке /promos — не больше этого, свежие сверху.
PROMO_LIST_LIMIT = 30


async def _promo_list(cfg: Config) -> tuple[str, InlineKeyboardMarkup]:
    data = await promocodes.listing(cfg)
    items = data["promocodes"][:PROMO_LIST_LIMIT]
    text = texts.promocode_list(data, promocodes.STATUSES)
    if data["total"] > len(items):
        text += "\n\nПоказаны %d новых из %d — остальные в Mini App." % (len(items), data["total"])
    return text, keyboards.promocodes_kb(items)


async def _promo_card(cfg: Config, promo: dict, confirm_delete: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    p = await promocodes.details(promo, cfg)
    return texts.promocode_card(p), keyboards.promocode_kb(p, confirm_delete)


@router.message(Command("promos"))
async def cmd_promos(message: Message, cfg: Config) -> None:
    text, kb = await _promo_list(cfg)
    await message.answer(text, reply_markup=kb)


@router.message(Command("promo"))
async def cmd_promo(message: Message, command: CommandObject, cfg: Config) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Использование: /promo КОД — карточка промокода. Все промокоды — /promos")
        return
    promo = await promocodes.find(raw)
    if not promo:
        await message.answer("Промокода «%s» нет. Все промокоды — /promos" % texts.e(raw))
        return
    text, kb = await _promo_card(cfg, promo)
    await message.answer(text, reply_markup=kb)


@router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Использование: /find NP-0001, /find 2b6f0cc9 или /find username")
        return
    orders = await db.list_orders(query=query, limit=10)
    if not orders:
        await message.answer("Ничего не нашли по «%s»." % texts.e(query))
        return
    for order in orders[:5]:
        text, kb = await _card(order)
        await message.answer(text, reply_markup=kb)


@router.message(Command("note"))
async def cmd_note(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /note NP-0001 текст заметки")
        return
    order = await db.get_order_by_code(parts[0])
    if not order:
        await message.answer("Заказ не найден.")
        return
    await db.set_note(order["id"], parts[1])
    await message.answer("Заметка сохранена для %s." % texts.e(texts.staff_code(order)))


@router.message(Command("log"))
async def cmd_log(message: Message, command: CommandObject) -> None:
    code = (command.args or "").strip()
    order = await db.get_order_by_code(code) if code else None
    if not order:
        await message.answer("Использование: /log NP-0001")
        return
    events = await db.list_events(order["id"], limit=20)
    lines = ["<b>История %s</b>" % texts.e(texts.staff_code(order))]
    for ev in reversed(events):
        lines.append(
            "%s · %s · %s"
            % (texts.when(ev["created_at"]), texts.e(ev["type"]), texts.e(ev["actor"]))
        )
    await message.answer("\n".join(lines))


@router.message(Command("chat"))
async def cmd_chat(message: Message, command: CommandObject) -> None:
    code = (command.args or "").strip()
    order = await db.get_order_by_code(code) if code else None
    if not order:
        await message.answer("Использование: /chat NP-0001")
        return
    messages = await db.list_messages(order["id"], limit=20)
    await db.mark_seen(order["id"], db.SELLER)
    await message.answer(
        texts.chat_history(order, messages), reply_markup=keyboards.seller_chat_kb(order["id"])
    )


@router.message(Command("reply"))
async def cmd_reply(message: Message, command: CommandObject, service: OrderService) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /reply NP-0001 текст сообщения")
        return
    order = await db.get_order_by_code(parts[0])
    if not order:
        await message.answer("Заказ не найден.")
        return
    try:
        await service.post_message(order["id"], parts[1], message.from_user.id, from_seller=True)
    except ServiceError as exc:
        await message.answer(texts.e(str(exc)))
        return
    await message.answer("Отправлено покупателю по заказу %s." % texts.e(texts.staff_code(order)))


@router.message(Command("sellerhelp"))
async def cmd_sellerhelp(message: Message) -> None:
    await message.answer(
        "<b>Команды продавца</b>\n\n"
        "/orders — открытые заказы (/orders all — все)\n"
        "/find NP-0001 — найти по коду, UDID или юзернейму\n"
        "/chat NP-0001 — переписка по заказу\n"
        "/reply NP-0001 текст — ответить покупателю\n"
        "/log NP-0001 — история заказа\n"
        "/note NP-0001 текст — заметка к заказу\n"
        "/stats — продажи и выручка: /stats 7d, /stats month, /stats 01.10-20.10\n"
        "/promos — промокоды, /promo КОД — карточка промокода\n\n"
        "Действия по заказу — кнопками под карточкой. "
        "Создать промокод или поменять его условия — в Mini App."
    )


# ----------------------------------------------------------------- кнопки


@router.callback_query(F.data.startswith("o:card:"))
async def cb_card(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    order = await db.get_order(parsed[1])
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    text, kb = await _card(order)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("o:noop:"))
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer("Ход покупателя — ждём UDID")


@router.callback_query(F.data.startswith("o:payok:"))
async def cb_payok(call: CallbackQuery, service: OrderService) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    try:
        order = await service.confirm_payment(parsed[1], _actor(call))
    except ServiceError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    text, kb = await _card(order)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Оплата подтверждена, у покупателя запрошен UDID")


@router.callback_query(F.data.startswith("o:payno:"))
async def cb_payno(call: CallbackQuery, service: OrderService) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    try:
        order = await service.reject_payment(parsed[1], _actor(call))
    except ServiceError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    text, kb = await _card(order)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Покупателю сообщили, что платёж не найден")


@router.callback_query(F.data.startswith("o:installed:"))
async def cb_installed(call: CallbackQuery, service: OrderService) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    try:
        order = await service.send_instruction(parsed[1], _actor(call))
    except ServiceError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    text, kb = await _card(order)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Инструкция отправлена покупателю")


@router.callback_query(F.data.startswith("o:scancel:"))
async def cb_scancel(call: CallbackQuery, service: OrderService) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    try:
        order = await service.cancel(parsed[1], _actor(call), by_seller=True)
    except ServiceError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    text, kb = await _card(order)
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer("Заказ отменён")


@router.callback_query(F.data.startswith("o:bell:"))
async def cb_bell(call: CallbackQuery) -> None:
    """Тишина по конкретному заказу, не трогая общие настройки продавца."""
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    order = await db.get_order(parsed[1])
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return

    current = await db.get_order_notify(call.from_user.id, order["id"])
    nxt = {None: False, False: True, True: None}[current]
    await db.set_order_notify(call.from_user.id, order["id"], nxt)

    mode = const.ORDER_NOTIFY_DEFAULT
    if nxt is True:
        mode = const.ORDER_NOTIFY_ON
    elif nxt is False:
        mode = const.ORDER_NOTIFY_OFF
    await call.answer("%s: %s" % (order["code"], const.ORDER_NOTIFY_TITLES[mode]), show_alert=True)


async def _edit(call: CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    """Правим сообщение на месте; старое (больше 48 часов) Telegram не даст — шлём новое."""
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as exc:
        # «Обновить» без изменений: карточка и так актуальна, второе сообщение не нужно.
        if "not modified" in str(exc):
            return
        await call.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("pc:"))
async def cb_promo(call: CallbackQuery, cfg: Config) -> None:
    parsed = keyboards.parse_promo_cb(call.data)
    if not parsed:
        await call.answer()
        return
    action, promo_id = parsed
    if action == "list":
        text, kb = await _promo_list(cfg)
        await _edit(call, text, kb)
        await call.answer()
        return

    actor = _actor(call)
    try:
        if action in ("on", "off"):
            promo = await promocodes.set_enabled(promo_id, action == "on", actor)
            note = "Промокод включён" if action == "on" else "Промокод выключен"
        elif action == "delyes":
            old = await promocodes.remove(promo_id, actor)
            text, kb = await _promo_list(cfg)
            await _edit(call, "🗑 Промокод <code>%s</code> удалён.\n\n%s" % (texts.e(old["code"]), text), kb)
            await call.answer("Промокод удалён")
            return
        else:
            promo = await promocodes.get(promo_id)
            note = "Удалить промокод насовсем?" if action == "del" else ""
    except promocodes.PromoError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    text, kb = await _promo_card(cfg, promo, confirm_delete=action == "del")
    await _edit(call, text, kb)
    await call.answer(note)


@router.callback_query(F.data.startswith("o:chat:"))
async def cb_chat(call: CallbackQuery, state: FSMContext) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    order = await db.get_order(parsed[1])
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return

    messages = await db.list_messages(order["id"], limit=10)
    await db.mark_seen(order["id"], db.SELLER)
    await state.set_state(SellerFlow.reply)
    await state.update_data(order_id=order["id"])
    await call.message.answer(
        texts.chat_history(order, messages)
        + "\n\nПришлите ответ одним сообщением — он уйдёт покупателю.",
        reply_markup=keyboards.cancel_kb(order["id"]),
    )
    await call.answer()


@router.message(StateFilter(SellerFlow.reply), F.text)
async def take_reply(message: Message, state: FSMContext, service: OrderService) -> None:
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.clear()
        await message.answer("Не понял, к какому заказу это относится. Откройте карточку заново.")
        return
    try:
        await service.post_message(order_id, message.text, message.from_user.id, from_seller=True)
    except ServiceError as exc:
        await message.answer(texts.e(str(exc)))
        return
    await state.clear()
    order = await db.get_order(order_id)
    await message.answer(
        "Отправлено покупателю по заказу %s." % texts.e(texts.staff_code(order) if order else ""),
        reply_markup=keyboards.seller_chat_kb(order_id),
    )
