from __future__ import annotations

from datetime import timedelta, timezone

NEW = "new"
PAYMENT_CHECK = "payment_check"
PAID = "paid"
UDID = "udid"
INSTALLED = "installed"
DONE = "done"
CANCELLED = "cancelled"

ACTIVE_STATUSES = (NEW, PAYMENT_CHECK, PAID, UDID, INSTALLED, DONE)
OPEN_STATUSES = (NEW, PAYMENT_CHECK, PAID, UDID, INSTALLED)

# ------------------------------------------------------------------- роли

# Покупатель оформляет заказы, продавец ими управляет, поставщик видит
# только UDID: номер заказа, покупателя, время создания и сам номер устройства.
ROLE_BUYER = "buyer"
ROLE_SELLER = "seller"
ROLE_SUPPLIER = "supplier"

# Подпись статуса: для покупателя и для продавца.
TITLES = {
    NEW: "Ожидает оплаты",
    PAYMENT_CHECK: "Оплата на проверке",
    PAID: "Нужен UDID",
    UDID: "Ставим сертификат",
    INSTALLED: "Готовим инструкцию",
    DONE: "Готово",
    CANCELLED: "Отменён",
}

# Шаг воронки (1..4), по которому подсвечивается прогресс в Mini App.
STEP = {
    NEW: 1,
    PAYMENT_CHECK: 1,
    PAID: 2,
    UDID: 3,
    INSTALLED: 4,
    DONE: 5,
    CANCELLED: 0,
}

STEP_NAMES = ("Оплата", "UDID", "Установка", "Инструкция")

# Продавцу и поставщику к номеру заказа дописывается номер за сутки: «NP-0155 (4)».
# Сутки одни на всю команду — по Новосибирску, иначе у одного и того же заказа
# у людей в разных поясах были бы разные номера. Летнего времени в Новосибирске
# нет с 2016 года, поэтому хватает постоянного смещения — без tzdata в образе.
ORDER_DAY_TZ = timezone(timedelta(hours=7))

# ---------------------------------------------------------------- документы

# Платёжные системы требуют, чтобы покупатель видел эти документы до оплаты.
# Ссылки задаются в .env (OFFER_URL, PRIVACY_URL, REFUND_URL); пока их нет,
# раздел «О нас» честно показывает, что документ готовится.
DOCUMENTS = (
    ("offer", "Публичная оферта", "Условия оказания услуги"),
    ("refund", "Возврат и отмена", "Когда и как вернуть деньги"),
    ("privacy", "Политика обработки персональных данных", "Какие данные собираем и зачем"),
    ("consent", "Согласие на обработку данных", "Что подтверждаете при оформлении заказа"),
)

# ------------------------------------------------------------- уведомления

NOTIFY_STATUS = "status"
NOTIFY_CHAT = "chat"
NOTIFY_NEW_ORDER = "new_order"
NOTIFY_PAYMENT = "payment"
NOTIFY_UDID = "udid_received"
NOTIFY_HELP = "help"

# (ключ, заголовок, пояснение) — порядок сохраняется в настройках.
BUYER_NOTIFICATIONS = (
    (NOTIFY_STATUS, "Статус заказа", "Оплата подтверждена, сертификат установлен, инструкция готова"),
    (NOTIFY_CHAT, "Сообщения продавца", "Ответы в переписке по заказу"),
)

SELLER_NOTIFICATIONS = (
    (NOTIFY_NEW_ORDER, "Новые заказы", "Кто-то оформил заказ"),
    (NOTIFY_PAYMENT, "Оплаты", "Покупатель приложил чек об оплате"),
    (NOTIFY_UDID, "Полученные UDID", "Покупатель прислал номер устройства"),
    (NOTIFY_CHAT, "Сообщения покупателей", "Вопросы по заказам"),
    (NOTIFY_HELP, "Запросы помощи", "Кнопка «Помощь по заказу»"),
)


# Поставщику важен только факт нового заказа — UDID он смотрит сам.
SUPPLIER_NOTIFICATIONS = (
    (NOTIFY_NEW_ORDER, "Новые заказы", "Кто-то оформил заказ"),
)


def notifications_for(role: str) -> tuple[tuple[str, str, str], ...]:
    if role == ROLE_SELLER:
        return SELLER_NOTIFICATIONS
    if role == ROLE_SUPPLIER:
        return SUPPLIER_NOTIFICATIONS
    return BUYER_NOTIFICATIONS


def notification_title(kind: str) -> str:
    for key, title, _hint in SELLER_NOTIFICATIONS + BUYER_NOTIFICATIONS:
        if key == kind:
            return title
    return kind


# Режимы для отдельного заказа: значение по умолчанию берётся из общих настроек.
ORDER_NOTIFY_DEFAULT = "default"
ORDER_NOTIFY_ON = "on"
ORDER_NOTIFY_OFF = "off"

# Полноэкранные эффекты сообщений Telegram (Bot API 7.4+, только личные чаты).
# Идентификаторы стандартных эффектов: клиент без поддержки покажет сообщение
# без анимации, а если Telegram эффект отклонит, сообщение уйдёт без него.
EFFECT_CONFETTI = "5046509860389126442"  # 🎉
EFFECT_FIRE = "5104841245755180586"  # 🔥

ORDER_NOTIFY_TITLES = {
    ORDER_NOTIFY_DEFAULT: "По общим настройкам",
    ORDER_NOTIFY_ON: "Всегда уведомлять",
    ORDER_NOTIFY_OFF: "Не беспокоить",
}
