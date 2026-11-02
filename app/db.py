from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

import aiosqlite

from . import const

log = logging.getLogger(__name__)

_conn: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    is_blocked  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT    NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    status       TEXT    NOT NULL,
    price_rub    INTEGER NOT NULL,
    payment_mode TEXT    NOT NULL,
    app_slug     TEXT,
    payment_ref  TEXT,
    device_udid  TEXT,
    instruction  TEXT,
    seller_note  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    paid_at      TEXT,
    udid_at      TEXT,
    installed_at TEXT,
    done_at      TEXT,
    -- Покупатель убрал выполненный заказ с экрана; для продавца он остаётся выполненным.
    closed_at    TEXT,
    -- Заказ тестировщика из TEST_IDS: ставится при создании и не меняется.
    is_test      INTEGER NOT NULL DEFAULT 0,
    -- Промокод: price_rub уже со скидкой, promo_discount — сколько рублей она сняла.
    -- promo_id есть, пока заказ занимает применение; у отменённого до оплаты он
    -- снят, а код и скидка остаются для истории.
    promo_id       INTEGER REFERENCES promo_codes(id) ON DELETE SET NULL,
    promo_code     TEXT,
    promo_discount INTEGER NOT NULL DEFAULT 0,
    -- Номер заказа за сутки по const.ORDER_DAY_TZ — продавцу «NP-0155 (4)».
    -- Ставится при создании и не меняется; у тестовых заказов свой счёт.
    day_no         INTEGER
);

CREATE INDEX IF NOT EXISTS idx_orders_user   ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_orders_code   ON orders(code);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    actor      TEXT NOT NULL,
    type       TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL,
    -- История промокода: создан, изменён, включён, выключен.
    promo_id   INTEGER REFERENCES promo_codes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_order ON events(order_id, id);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    author      TEXT    NOT NULL,
    author_id   INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    seen_buyer  INTEGER NOT NULL DEFAULT 0,
    seen_seller INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_order ON messages(order_id, id);

CREATE TABLE IF NOT EXISTS notify_prefs (
    user_id INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    enabled INTEGER NOT NULL,
    PRIMARY KEY (user_id, kind)
);

CREATE TABLE IF NOT EXISTS notify_order_prefs (
    user_id  INTEGER NOT NULL,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    enabled  INTEGER NOT NULL,
    PRIMARY KEY (user_id, order_id)
);

-- Платежи через платёжный сервис (Platega): одна строка на каждую выданную
-- ссылку. У заказа их может быть несколько — ссылка живёт 15 минут.
-- При удалении заказа платёж остаётся: деньги-то были.
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER REFERENCES orders(id) ON DELETE SET NULL,
    provider    TEXT    NOT NULL,
    -- Номер транзакции у платёжного сервиса.
    external_id TEXT    NOT NULL UNIQUE,
    -- Сумма в рублях, на которую выдана ссылка.
    amount      INTEGER NOT NULL,
    -- PENDING, CONFIRMED, CANCELED, CHARGEBACKED, PARTIALLY_REFUNDED.
    status      TEXT    NOT NULL,
    url         TEXT,
    -- После этого момента (UTC) ссылку покупателю не показываем — выдаём новую.
    expires_at  TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id, id);

-- Промокоды: создают и правят продавцы, публикуют сами, где нужно.
CREATE TABLE IF NOT EXISTS promo_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Заглавными буквами; после создания не меняется — код уже опубликован.
    code       TEXT    NOT NULL UNIQUE,
    -- percent — скидка в процентах (1–99), rub — в рублях.
    kind       TEXT    NOT NULL,
    value      INTEGER NOT NULL,
    -- Даты по Москве (2026-11-30), обе включительно; NULL — без ограничения.
    starts_on  TEXT,
    ends_on    TEXT,
    -- Сколько применений всего и на одного покупателя; NULL — без лимита.
    max_uses   INTEGER,
    per_user   INTEGER,
    -- Сколько раз применён. Пока промокоды к заказам не применяются — всегда 0.
    used       INTEGER NOT NULL DEFAULT 0,
    enabled    INTEGER NOT NULL DEFAULT 1,
    -- Для продавцов: где опубликован, для кого. Покупатель её не видит.
    note       TEXT,
    created_by TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _order_day(ts: str) -> tuple[str, str]:
    """Сутки по const.ORDER_DAY_TZ, в которые попал момент ts, — границами в UTC, как в базе."""
    local = datetime.fromisoformat(ts).astimezone(const.ORDER_DAY_TZ)
    start = datetime.combine(local.date(), time(0, 0), tzinfo=const.ORDER_DAY_TZ)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat(timespec="seconds"),
        end.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


# Тот же сдвиг для SQLite: date(created_at, '+420 minutes') — дата по const.ORDER_DAY_TZ.
_ORDER_DAY_SHIFT = "%+d minutes" % (const.ORDER_DAY_TZ.utcoffset(None) // timedelta(minutes=1))


def conn() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("База не инициализирована: вызовите db.init()")
    return _conn


async def init(path: str) -> None:
    global _conn
    _conn = await aiosqlite.connect(path)
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("PRAGMA foreign_keys=ON")
    await _conn.execute("PRAGMA busy_timeout=5000")
    await _conn.executescript(SCHEMA)
    await _conn.commit()
    await _migrate()
    log.info("База готова: %s", path)


async def _migrate() -> None:
    """Базы, созданные до перехода с IMEI на UDID, переименовывают свои колонки.

    CREATE TABLE IF NOT EXISTS существующую таблицу не трогает, поэтому старую
    схему нужно поправить отдельно. Операция идемпотентная.
    """
    columns = {row["name"] for row in await _fetchall("PRAGMA table_info(orders)")}
    renames = (("imei", "device_udid"), ("imei_at", "udid_at"))
    for old, new in renames:
        if old in columns and new not in columns:
            await conn().execute("ALTER TABLE orders RENAME COLUMN %s TO %s" % (old, new))
            log.info("Миграция: колонка %s переименована в %s", old, new)
    await conn().execute("UPDATE orders SET status = ? WHERE status = 'imei'", (const.UDID,))
    # Заказы до выбора отдельных приложений — это заказы всего каталога (NULL).
    if "app_slug" not in columns:
        await conn().execute("ALTER TABLE orders ADD COLUMN app_slug TEXT")
        log.info("Миграция: добавлена колонка orders.app_slug")
    if "closed_at" not in columns:
        await conn().execute("ALTER TABLE orders ADD COLUMN closed_at TEXT")
        log.info("Миграция: добавлена колонка orders.closed_at")
    if "is_test" not in columns:
        await conn().execute("ALTER TABLE orders ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")
        log.info("Миграция: добавлена колонка orders.is_test")
    if "day_no" not in columns:
        await conn().execute("ALTER TABLE orders ADD COLUMN day_no INTEGER")
        # Прежним заказам — номер по порядку внутри своих суток, как у новых.
        await conn().execute(
            """
            UPDATE orders SET day_no = (
                SELECT n FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY is_test, date(created_at, ?) ORDER BY id
                    ) AS n
                    FROM orders
                ) AS ranked
                WHERE ranked.id = orders.id
            )
            """,
            (_ORDER_DAY_SHIFT,),
        )
        log.info("Миграция: добавлена колонка orders.day_no")
    promo_columns = (
        ("promo_id", "INTEGER REFERENCES promo_codes(id) ON DELETE SET NULL"),
        ("promo_code", "TEXT"),
        ("promo_discount", "INTEGER NOT NULL DEFAULT 0"),
    )
    for name, kind in promo_columns:
        if name not in columns:
            await conn().execute("ALTER TABLE orders ADD COLUMN %s %s" % (name, kind))
            log.info("Миграция: добавлена колонка orders.%s", name)
    await conn().execute("CREATE INDEX IF NOT EXISTS idx_orders_promo ON orders(promo_id, user_id)")
    events = {row["name"] for row in await _fetchall("PRAGMA table_info(events)")}
    if "promo_id" not in events:
        await conn().execute(
            "ALTER TABLE events ADD COLUMN promo_id INTEGER REFERENCES promo_codes(id) ON DELETE CASCADE"
        )
        log.info("Миграция: добавлена колонка events.promo_id")
    # Индекс — здесь, а не в SCHEMA: в старой базе колонка появляется только сейчас.
    await conn().execute("CREATE INDEX IF NOT EXISTS idx_events_promo ON events(promo_id, id)")
    await conn().commit()


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def _fetchone(sql: str, args: Iterable[Any] = ()) -> dict | None:
    async with conn().execute(sql, tuple(args)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _fetchall(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    async with conn().execute(sql, tuple(args)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- users


async def upsert_user(tg_id: int, username: str | None, first_name: str | None) -> None:
    ts = now()
    await conn().execute(
        """
        INSERT INTO users (tg_id, username, first_name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tg_id) DO UPDATE SET
            username   = excluded.username,
            first_name = excluded.first_name,
            updated_at = excluded.updated_at
        """,
        (tg_id, username, first_name, ts, ts),
    )
    await conn().commit()


async def get_user(tg_id: int) -> dict | None:
    return await _fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))


async def set_blocked(tg_id: int, blocked: bool) -> None:
    await conn().execute(
        "UPDATE users SET is_blocked = ?, updated_at = ? WHERE tg_id = ?",
        (1 if blocked else 0, now(), tg_id),
    )
    await conn().commit()


# --------------------------------------------------------------- orders


async def create_order(
    user_id: int,
    price_rub: int,
    payment_mode: str,
    prefix: str,
    app_slug: str | None = None,
    is_test: bool = False,
) -> dict:
    ts = now()
    day_start, day_end = _order_day(ts)
    # Номер за сутки — следующий после наибольшего, а не число заказов: если
    # продавец удалит заказ из середины дня, номера не повторятся.
    cur = await conn().execute(
        """
        INSERT INTO orders (code, user_id, status, price_rub, payment_mode, app_slug, is_test,
                            created_at, updated_at, day_no)
        VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, (
            SELECT COALESCE(MAX(day_no), 0) + 1 FROM orders
            WHERE is_test = ? AND created_at >= ? AND created_at < ?
        ))
        """,
        (user_id, const.NEW, price_rub, payment_mode, app_slug, int(is_test), ts, ts,
         int(is_test), day_start, day_end),
    )
    order_id = int(cur.lastrowid)
    code = "%s-%04d" % (prefix, order_id)
    await conn().execute("UPDATE orders SET code = ? WHERE id = ?", (code, order_id))
    await conn().commit()
    await add_event(order_id, "user:%d" % user_id, "created", None)
    order = await get_order(order_id)
    assert order is not None
    return order


async def set_order_product(
    order_id: int, app_slug: str | None, price_rub: int, promo_discount: int = 0
) -> dict | None:
    """Смена товара или цены у неоплаченного заказа: покупатель передумал до
    оплаты, сменилась цена или акция. promo_discount — скидка промокода в рублях."""
    await conn().execute(
        "UPDATE orders SET app_slug = ?, price_rub = ?, promo_discount = ?, updated_at = ? WHERE id = ?",
        (app_slug, price_rub, promo_discount, now(), order_id),
    )
    await conn().commit()
    await add_event(order_id, "system", "product", app_slug or "catalog")
    return await get_order(order_id)


async def get_order(order_id: int) -> dict | None:
    return await _fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))


async def get_order_by_code(code: str) -> dict | None:
    return await _fetchone("SELECT * FROM orders WHERE code = ? COLLATE NOCASE", (code,))


async def get_active_order(user_id: int) -> dict | None:
    """Последний незакрытый заказ пользователя, иначе последний вообще."""
    marks = ",".join("?" * len(const.OPEN_STATUSES))
    row = await _fetchone(
        "SELECT * FROM orders WHERE user_id = ? AND status IN (%s) ORDER BY id DESC LIMIT 1" % marks,
        (user_id, *const.OPEN_STATUSES),
    )
    if row:
        return row
    return await _fetchone(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    )


async def list_user_orders(user_id: int, limit: int = 20) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    )


# Сортировки списка продавца: ключ из витрины → ORDER BY.
_UNREAD = "(SELECT COUNT(*) FROM messages m WHERE m.order_id = o.id AND m.seen_seller = 0)"
_BUYER = "COALESCE(u.username, u.first_name)"
ORDER_SORTS = {
    "created_desc": "o.id DESC",
    "created_asc": "o.id ASC",
    # updated_at двигают смена статуса, чек, UDID и каждое сообщение.
    "activity": "o.updated_at DESC, o.id DESC",
    "unread": "%s DESC, o.id DESC" % _UNREAD,
    # Безымянные — в конце; латиница без учёта регистра.
    "buyer": "%s IS NULL, %s COLLATE NOCASE, o.id DESC" % (_BUYER, _BUYER),
}


def _order_filters(
    status: str | None,
    query: str | None,
    statuses: list[str] | None,
    created_from: str | None,
    created_to: str | None,
    unread_only: bool,
    has_udid: bool | None,
    hide_test: bool = False,
) -> tuple[str, list[Any]]:
    """WHERE для списка заказов. Даты — в формате базы (UTC ISO), как пишет now()."""
    sql = " WHERE 1 = 1"
    args: list[Any] = []

    if status == "open":
        allowed = list(const.OPEN_STATUSES)
    elif status:
        allowed = [status]
    else:
        allowed = None  # все статусы
    if statuses:
        allowed = [s for s in (allowed or list(const.TITLES)) if s in statuses]
    if allowed is not None:
        if not allowed:
            return " WHERE 1 = 0", []
        sql += " AND o.status IN (%s)" % ",".join("?" * len(allowed))
        args += allowed

    if query:
        like = "%" + query.strip() + "%"
        sql += (
            " AND (o.code LIKE ? OR o.device_udid LIKE ? OR u.username LIKE ?"
            " OR CAST(o.user_id AS TEXT) LIKE ?)"
        )
        args += [like, like, like, like]
    if created_from:
        sql += " AND o.created_at >= ?"
        args.append(created_from)
    if created_to:
        sql += " AND o.created_at < ?"
        args.append(created_to)
    if unread_only:
        sql += " AND EXISTS (SELECT 1 FROM messages m WHERE m.order_id = o.id AND m.seen_seller = 0)"
    if has_udid is True:
        sql += " AND o.device_udid IS NOT NULL AND o.device_udid <> ''"
    elif has_udid is False:
        sql += " AND (o.device_udid IS NULL OR o.device_udid = '')"
    if hide_test:
        sql += " AND o.is_test = 0"
    return sql, args


async def list_orders(
    status: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
    *,
    sort: str = "created_desc",
    statuses: list[str] | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    unread_only: bool = False,
    has_udid: bool | None = None,
    hide_test: bool = False,
) -> list[dict]:
    where, args = _order_filters(
        status, query, statuses, created_from, created_to, unread_only, has_udid, hide_test
    )
    sql = (
        "SELECT o.*, u.username, u.first_name "
        "FROM orders o LEFT JOIN users u ON u.tg_id = o.user_id"
        + where
        + " ORDER BY " + ORDER_SORTS.get(sort, ORDER_SORTS["created_desc"])
        + " LIMIT ? OFFSET ?"
    )
    return await _fetchall(sql, args + [limit, offset])


async def count_orders(
    status: str | None = None,
    query: str | None = None,
    *,
    statuses: list[str] | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    unread_only: bool = False,
    has_udid: bool | None = None,
    hide_test: bool = False,
) -> int:
    """Сколько заказов подходит под фильтры — чтобы не прятать молча тех, кто не влез в список."""
    where, args = _order_filters(
        status, query, statuses, created_from, created_to, unread_only, has_udid, hide_test
    )
    row = await _fetchone(
        "SELECT COUNT(*) AS n FROM orders o LEFT JOIN users u ON u.tg_id = o.user_id" + where, args
    )
    return int(row["n"]) if row else 0


async def list_orders_with_udid(limit: int = 50) -> list[dict]:
    """Заказы с присланным UDID — рабочая очередь поставщика."""
    return await _fetchall(
        "SELECT o.*, u.username, u.first_name "
        "FROM orders o LEFT JOIN users u ON u.tg_id = o.user_id "
        "WHERE o.device_udid IS NOT NULL AND o.device_udid <> '' "
        "ORDER BY o.id DESC LIMIT ?",
        (limit,),
    )


async def status_counts() -> dict[str, int]:
    """Счётчики по статусам — только настоящие заказы, тестовые не считаются."""
    rows = await _fetchall("SELECT status, COUNT(*) AS n FROM orders WHERE is_test = 0 GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


async def count_test_orders() -> int:
    row = await _fetchone("SELECT COUNT(*) AS n FROM orders WHERE is_test = 1")
    return int(row["n"]) if row else 0


_TIMESTAMP_FIELD = {
    const.PAID: "paid_at",
    const.UDID: "udid_at",
    const.INSTALLED: "installed_at",
    const.DONE: "done_at",
}


async def set_status(
    order_id: int, status: str, actor: str, detail: str | None = None
) -> dict | None:
    ts = now()
    field = _TIMESTAMP_FIELD.get(status)
    sql = "UPDATE orders SET status = ?, updated_at = ?"
    args: list[Any] = [status, ts]
    if field:
        # COALESCE: первая отметка времени не переписывается при повторном переходе.
        sql += ", %s = COALESCE(%s, ?)" % (field, field)
        args.append(ts)
    sql += " WHERE id = ?"
    args.append(order_id)
    await conn().execute(sql, args)
    await conn().commit()
    await add_event(order_id, actor, "status:" + status, detail)
    return await get_order(order_id)


async def close_order(order_id: int, actor: str) -> dict | None:
    """Покупатель закрыл выполненный заказ: статус не меняется, заказ уходит с его экрана."""
    ts = now()
    await conn().execute(
        "UPDATE orders SET closed_at = ?, updated_at = ? WHERE id = ?", (ts, ts, order_id)
    )
    await conn().commit()
    await add_event(order_id, actor, "closed", "покупатель закрыл выполненный заказ")
    return await get_order(order_id)


async def stale_unpaid(before: str) -> list[dict]:
    """Неоплаченные заказы, которые не менялись с момента before (UTC ISO).

    reminded — напоминание об оплате уже ушло после последнего изменения заказа:
    любое действие покупателя сдвигает updated_at, и отсчёт начинается заново.
    """
    return await _fetchall(
        "SELECT o.*, EXISTS(SELECT 1 FROM events e WHERE e.order_id = o.id AND e.type = 'expire_reminder' "
        "AND e.created_at >= o.updated_at) AS reminded "
        "FROM orders o WHERE o.status = ? AND o.updated_at < ? ORDER BY o.id",
        (const.NEW, before),
    )


async def expire_order(order_id: int, before: str, detail: str) -> dict | None:
    """Бот отменяет неоплаченный заказ — только если он всё ещё не оплачен и не менялся.

    Чек или сообщение, пришедшие в ту же секунду, отмену остановят: None.
    """
    ts = now()
    cur = await conn().execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ? AND status = ? AND updated_at < ?",
        (const.CANCELLED, ts, order_id, const.NEW, before),
    )
    await conn().commit()
    if not cur.rowcount:
        return None
    await add_event(order_id, "system", "status:" + const.CANCELLED, detail)
    return await get_order(order_id)


async def set_payment_mode(order_id: int, mode: str) -> dict | None:
    """Режим оплаты неоплаченного заказа — за текущим PAYMENT_MODE, когда его переключили."""
    await conn().execute(
        "UPDATE orders SET payment_mode = ?, updated_at = ? WHERE id = ?", (mode, now(), order_id)
    )
    await conn().commit()
    await add_event(order_id, "system", "payment_mode", mode)
    return await get_order(order_id)


# ------------------------------------------------------- платёжный сервис


async def create_payment(
    order_id: int, provider: str, external_id: str, amount: int, status: str, url: str, expires_at: str | None
) -> dict:
    ts = now()
    cur = await conn().execute(
        "INSERT INTO payments (order_id, provider, external_id, amount, status, url, expires_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, provider, external_id, amount, status, url, expires_at, ts, ts),
    )
    await conn().execute(
        "UPDATE orders SET payment_ref = ?, updated_at = ? WHERE id = ?", (external_id, ts, order_id)
    )
    await conn().commit()
    await add_event(order_id, "system", "payment_link", "%s %s · %d ₽" % (provider, external_id, amount))
    row = await _fetchone("SELECT * FROM payments WHERE id = ?", (int(cur.lastrowid),))
    assert row is not None
    return row


async def get_payment(external_id: str) -> dict | None:
    return await _fetchone("SELECT * FROM payments WHERE external_id = ?", (external_id,))


async def last_payment(order_id: int) -> dict | None:
    return await _fetchone(
        "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,)
    )


async def order_payments(order_id: int, status: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM payments WHERE order_id = ?", [order_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    return await _fetchall(sql + " ORDER BY id DESC", args)


async def pending_payments(since: str) -> list[dict]:
    """Неоплаченные ссылки, выданные после since, — их статус спрашиваем сами,
    если уведомление от платёжного сервиса не дошло."""
    return await _fetchall(
        "SELECT * FROM payments WHERE status = 'PENDING' AND created_at >= ? ORDER BY id", (since,)
    )


async def set_payment_status(payment_id: int, status: str) -> None:
    await conn().execute(
        "UPDATE payments SET status = ?, updated_at = ? WHERE id = ?", (status, now(), payment_id)
    )
    await conn().commit()


async def set_payment_ref(order_id: int, ref: str) -> None:
    await conn().execute(
        "UPDATE orders SET payment_ref = ?, updated_at = ? WHERE id = ?", (ref, now(), order_id)
    )
    await conn().commit()


async def set_udid(order_id: int, udid: str, actor: str) -> dict | None:
    await conn().execute(
        "UPDATE orders SET device_udid = ?, updated_at = ? WHERE id = ?", (udid, now(), order_id)
    )
    await conn().commit()
    return await set_status(order_id, const.UDID, actor, "UDID получен")


async def set_note(order_id: int, note: str) -> None:
    await conn().execute(
        "UPDATE orders SET seller_note = ?, updated_at = ? WHERE id = ?", (note, now(), order_id)
    )
    await conn().commit()


async def delete_order(order_id: int, actor: str) -> dict | None:
    """Продавец удаляет заказ из базы — например, старый или тестовый.

    Переписка, история и настройки уведомлений по заказу уходят каскадом.
    Сам факт удаления остаётся в журнале событий без привязки к заказу:
    кто, когда и что удалил.
    """
    order = await get_order(order_id)
    if not order:
        return None
    if order.get("promo_id"):
        # Заказ занимал применение промокода — вместе с заказом оно возвращается.
        await conn().execute(
            "UPDATE promo_codes SET used = used - 1 WHERE id = ? AND used > 0", (order["promo_id"],)
        )
    await conn().execute("DELETE FROM orders WHERE id = ?", (order_id,))
    await conn().execute(
        "INSERT INTO events (order_id, actor, type, detail, created_at) VALUES (NULL, ?, 'order_deleted', ?, ?)",
        (actor, "%s · %s · %s ₽" % (order["code"], order["status"], order["price_rub"]), now()),
    )
    await conn().commit()
    return order


# Служебный покупатель, на которого переходят заказы после /forget.
ANON_USER_ID = 0
ANON_NAME = "Удалённый покупатель"


async def forget_user(user_id: int) -> dict:
    """Обезличивает покупателя по команде /forget.

    Заказы целиком не удаляем: без них пропадут продажи и выручка в аналитике.
    Заказ переходит на служебного «Удалённого покупателя», из него стираются
    UDID, заметка продавца и ссылка на платёж, удаляются переписка и записи
    о чеках (в них имена файлов и ссылки), а Telegram ID покупателя уходит
    и из журнала событий. Остаются номер, сумма, даты и статусы.
    Сам пользователь и его настройки удаляются.
    """
    ts = now()
    rows = await _fetchall("SELECT id FROM orders WHERE user_id = ?", (user_id,))
    ids = [row["id"] for row in rows]
    if ids:
        marks = ",".join("?" * len(ids))
        await conn().execute(
            "INSERT OR IGNORE INTO users (tg_id, username, first_name, created_at, updated_at) "
            "VALUES (?, NULL, ?, ?, ?)",
            (ANON_USER_ID, ANON_NAME, ts, ts),
        )
        await conn().execute("DELETE FROM messages WHERE order_id IN (%s)" % marks, ids)
        await conn().execute(
            "DELETE FROM events WHERE order_id IN (%s) AND type IN ('receipt', 'message')" % marks, ids
        )
        await conn().execute(
            "UPDATE events SET actor = ? WHERE order_id IN (%s) AND actor = ?" % marks,
            ["user:%d" % ANON_USER_ID, *ids, "user:%d" % user_id],
        )
        await conn().execute(
            "UPDATE orders SET user_id = ?, device_udid = NULL, seller_note = NULL, instruction = NULL, "
            "payment_ref = NULL, updated_at = ? WHERE id IN (%s)" % marks,
            [ANON_USER_ID, ts, *ids],
        )
        await conn().executemany(
            "INSERT INTO events (order_id, actor, type, detail, created_at) VALUES (?, 'system', 'forget', ?, ?)",
            [(oid, "данные покупателя обезличены по /forget", ts) for oid in ids],
        )
    await conn().execute("DELETE FROM notify_order_prefs WHERE user_id = ?", (user_id,))
    await conn().execute("DELETE FROM notify_prefs WHERE user_id = ?", (user_id,))
    cur = await conn().execute("DELETE FROM users WHERE tg_id = ?", (user_id,))
    await conn().commit()
    return {"user": bool(cur.rowcount), "orders": len(ids)}


# --------------------------------------------------------------- events


async def add_event(order_id: int | None, actor: str, type_: str, detail: str | None) -> None:
    await conn().execute(
        "INSERT INTO events (order_id, actor, type, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (order_id, actor, type_, detail, now()),
    )
    await conn().commit()


async def list_events(order_id: int, limit: int = 50) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM events WHERE order_id = ? ORDER BY id DESC LIMIT ?", (order_id, limit)
    )


# ------------------------------------------------------------ промокоды

# Поля, которые продавец задаёт и правит. Сам код и счётчик used сюда не входят.
PROMO_FIELDS = ("kind", "value", "starts_on", "ends_on", "max_uses", "per_user", "enabled", "note")


async def _promo_event(promo_id: int | None, actor: str, type_: str, detail: str | None, ts: str) -> None:
    await conn().execute(
        "INSERT INTO events (order_id, promo_id, actor, type, detail, created_at) VALUES (NULL, ?, ?, ?, ?, ?)",
        (promo_id, actor, type_, detail, ts),
    )


async def list_promocodes() -> list[dict]:
    return await _fetchall("SELECT * FROM promo_codes ORDER BY id DESC")


async def get_promocode(promo_id: int) -> dict | None:
    return await _fetchone("SELECT * FROM promo_codes WHERE id = ?", (promo_id,))


async def get_promocode_by_code(code: str) -> dict | None:
    """Код хранится заглавными — ищем уже приведённый к ним."""
    return await _fetchone("SELECT * FROM promo_codes WHERE code = ?", (code,))


async def create_promocode(code: str, fields: dict, actor: str, detail: str | None) -> dict:
    ts = now()
    cur = await conn().execute(
        "INSERT INTO promo_codes (code, %s, created_by, created_at, updated_at) VALUES (?, %s, ?, ?, ?)"
        % (", ".join(PROMO_FIELDS), ", ".join("?" * len(PROMO_FIELDS))),
        (code, *(fields[key] for key in PROMO_FIELDS), actor, ts, ts),
    )
    promo_id = int(cur.lastrowid)
    await _promo_event(promo_id, actor, "promo_created", detail, ts)
    await conn().commit()
    promo = await get_promocode(promo_id)
    assert promo is not None
    return promo


async def update_promocode(promo_id: int, fields: dict, actor: str, type_: str, detail: str | None) -> dict | None:
    """Меняет только переданные поля и пишет в историю, что поменялось."""
    unknown = set(fields) - set(PROMO_FIELDS)
    if unknown:
        raise ValueError("Эти поля промокода не меняются: %s" % ", ".join(sorted(unknown)))
    ts = now()
    sets = ", ".join("%s = ?" % key for key in fields)
    await conn().execute(
        "UPDATE promo_codes SET %s, updated_at = ? WHERE id = ?" % sets, (*fields.values(), ts, promo_id)
    )
    await _promo_event(promo_id, actor, type_, detail, ts)
    await conn().commit()
    return await get_promocode(promo_id)


async def delete_promocode(promo_id: int, actor: str, detail: str) -> bool:
    """Удаляет промокод, который ни разу не применяли; его история уходит каскадом.

    Сам факт удаления остаётся в журнале без привязки к промокоду — кто,
    когда и что удалил. Применённый промокод не удаляется: False.
    """
    cur = await conn().execute("DELETE FROM promo_codes WHERE id = ? AND used = 0", (promo_id,))
    if not cur.rowcount:
        return False
    await _promo_event(None, actor, "promo_deleted", detail, now())
    await conn().commit()
    return True


async def list_promo_events(promo_id: int, limit: int = 30) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM events WHERE promo_id = ? ORDER BY id DESC LIMIT ?", (promo_id, limit)
    )


async def promo_uses_by_user(promo_id: int, user_id: int, skip_order: int | None = None) -> int:
    """Сколько заказов покупателя занимают применения промокода."""
    row = await _fetchone(
        "SELECT COUNT(*) AS n FROM orders WHERE promo_id = ? AND user_id = ? AND id != ?",
        (promo_id, user_id, skip_order or 0),
    )
    return int(row["n"]) if row else 0


async def hold_promocode(
    order: dict, promo: dict, price_rub: int, discount: int, actor: str
) -> bool:
    """Заказ занимает применение промокода — или заменяет им прежний.

    Лимиты «всего» и «на покупателя» проверяет сам UPDATE: два покупателя,
    нажавшие «Применить» одновременно, не возьмут одно последнее применение.
    False — лимит уже исчерпан.
    """
    ts = now()
    cur = await conn().execute(
        "UPDATE promo_codes SET used = used + 1 WHERE id = ? "
        "AND (max_uses IS NULL OR used < max_uses) "
        "AND (per_user IS NULL OR (SELECT COUNT(*) FROM orders "
        "WHERE promo_id = ? AND user_id = ? AND id != ?) < per_user)",
        (promo["id"], promo["id"], order["user_id"], order["id"]),
    )
    if not cur.rowcount:
        return False
    if order.get("promo_id"):
        await conn().execute(
            "UPDATE promo_codes SET used = used - 1 WHERE id = ? AND used > 0", (order["promo_id"],)
        )
    await conn().execute(
        "UPDATE orders SET promo_id = ?, promo_code = ?, promo_discount = ?, price_rub = ?, updated_at = ? "
        "WHERE id = ?",
        (promo["id"], promo["code"], discount, price_rub, ts, order["id"]),
    )
    await conn().execute(
        "INSERT INTO events (order_id, promo_id, actor, type, detail, created_at) VALUES (?, ?, ?, 'promo_applied', ?, ?)",
        (order["id"], promo["id"], actor, "%s → заказ %s · скидка %d ₽" % (promo["code"], order["code"], discount), ts),
    )
    await conn().commit()
    return True


async def release_promocode(order: dict, actor: str, reason: str, price_rub: int | None = None) -> dict | None:
    """Заказ отдаёт применение промокода обратно.

    price_rub задан — промокод снимается с заказа совсем (покупатель его убрал),
    и заказ стоит столько. Без него — заказ отменён до оплаты: код и скидка
    остаются в нём для истории, но применение уже свободно.
    """
    if not order.get("promo_id"):
        return order
    ts = now()
    await conn().execute(
        "UPDATE promo_codes SET used = used - 1 WHERE id = ? AND used > 0", (order["promo_id"],)
    )
    if price_rub is None:
        await conn().execute("UPDATE orders SET promo_id = NULL, updated_at = ? WHERE id = ?", (ts, order["id"]))
    else:
        await conn().execute(
            "UPDATE orders SET promo_id = NULL, promo_code = NULL, promo_discount = 0, price_rub = ?, "
            "updated_at = ? WHERE id = ?",
            (price_rub, ts, order["id"]),
        )
    await conn().execute(
        "INSERT INTO events (order_id, promo_id, actor, type, detail, created_at) VALUES (?, ?, ?, 'promo_released', ?, ?)",
        (order["id"], order["promo_id"], actor, "%s ← заказ %s · %s" % (order["promo_code"], order["code"], reason), ts),
    )
    await conn().commit()
    return await get_order(order["id"])


async def list_promo_orders(promo_id: int, limit: int = 50) -> list[dict]:
    """Заказы, которые занимают применения промокода, — для его карточки."""
    return await _fetchall(
        "SELECT o.*, u.username, u.first_name FROM orders o LEFT JOIN users u ON u.tg_id = o.user_id "
        "WHERE o.promo_id = ? ORDER BY o.id DESC LIMIT ?",
        (promo_id, limit),
    )


# ------------------------------------------------------------- переписка

BUYER = "buyer"
SELLER = "seller"


def _seen_column(viewer: str) -> str:
    return "seen_buyer" if viewer == BUYER else "seen_seller"


async def add_message(order_id: int, author: str, author_id: int, text: str) -> dict:
    """Своё сообщение автор видел сразу, второй стороне оно придёт непрочитанным."""
    ts = now()
    cur = await conn().execute(
        """
        INSERT INTO messages (order_id, author, author_id, text, created_at, seen_buyer, seen_seller)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            author,
            author_id,
            text,
            ts,
            1 if author == BUYER else 0,
            1 if author == SELLER else 0,
        ),
    )
    await conn().execute(
        "UPDATE orders SET updated_at = ? WHERE id = ?", (ts, order_id)
    )
    await conn().commit()
    row = await _fetchone("SELECT * FROM messages WHERE id = ?", (int(cur.lastrowid),))
    assert row is not None
    return row


async def list_messages(order_id: int, limit: int = 200) -> list[dict]:
    rows = await _fetchall(
        "SELECT * FROM messages WHERE order_id = ? ORDER BY id DESC LIMIT ?", (order_id, limit)
    )
    return list(reversed(rows))


async def mark_seen(order_id: int, viewer: str) -> None:
    column = _seen_column(viewer)
    await conn().execute(
        "UPDATE messages SET %s = 1 WHERE order_id = ? AND %s = 0" % (column, column),
        (order_id,),
    )
    await conn().commit()


async def unread_count(order_id: int, viewer: str) -> int:
    row = await _fetchone(
        "SELECT COUNT(*) AS n FROM messages WHERE order_id = ? AND %s = 0" % _seen_column(viewer),
        (order_id,),
    )
    return int(row["n"]) if row else 0


# ----------------------------------------------------------- уведомления


async def get_notify_prefs(user_id: int) -> dict[str, bool]:
    """Только явно изменённые виды; остальные включены по умолчанию."""
    rows = await _fetchall("SELECT kind, enabled FROM notify_prefs WHERE user_id = ?", (user_id,))
    return {r["kind"]: bool(r["enabled"]) for r in rows}


async def set_notify_pref(user_id: int, kind: str, enabled: bool) -> None:
    await conn().execute(
        """
        INSERT INTO notify_prefs (user_id, kind, enabled) VALUES (?, ?, ?)
        ON CONFLICT(user_id, kind) DO UPDATE SET enabled = excluded.enabled
        """,
        (user_id, kind, 1 if enabled else 0),
    )
    await conn().commit()


async def get_order_notify(user_id: int, order_id: int) -> bool | None:
    """True/False — заказ настроен отдельно, None — действуют общие настройки."""
    row = await _fetchone(
        "SELECT enabled FROM notify_order_prefs WHERE user_id = ? AND order_id = ?",
        (user_id, order_id),
    )
    return bool(row["enabled"]) if row else None


async def set_order_notify(user_id: int, order_id: int, enabled: bool | None) -> None:
    if enabled is None:
        await conn().execute(
            "DELETE FROM notify_order_prefs WHERE user_id = ? AND order_id = ?",
            (user_id, order_id),
        )
    else:
        await conn().execute(
            """
            INSERT INTO notify_order_prefs (user_id, order_id, enabled) VALUES (?, ?, ?)
            ON CONFLICT(user_id, order_id) DO UPDATE SET enabled = excluded.enabled
            """,
            (user_id, order_id, 1 if enabled else 0),
        )
    await conn().commit()


async def order_notify_map(user_id: int) -> dict[int, bool]:
    rows = await _fetchall(
        "SELECT order_id, enabled FROM notify_order_prefs WHERE user_id = ?", (user_id,)
    )
    return {int(r["order_id"]): bool(r["enabled"]) for r in rows}


async def unread_by_order(viewer: str, skip_test: bool = False) -> dict[int, int]:
    """Сколько непрочитанных в каждом заказе — для счётчиков в списке продавца.

    skip_test — без тестовых заказов: их переписка не должна зажигать общий счётчик.
    """
    rows = await _fetchall(
        "SELECT m.order_id, COUNT(*) AS n FROM messages m JOIN orders o ON o.id = m.order_id "
        "WHERE m.%s = 0%s GROUP BY m.order_id"
        % (_seen_column(viewer), " AND o.is_test = 0" if skip_test else "")
    )
    return {int(r["order_id"]): int(r["n"]) for r in rows}
