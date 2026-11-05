"""Резервные копии базы: python -m app.backup <команда>

    create                    снимок базы → зашифрованная копия в BACKUP_DIR,
                              лишние старые копии удаляются по правилу хранения
    list                      какие копии есть
    restore <файл>            заменить базу копией — только при остановленном
                              боте, поэтому на сервере: ./backup.sh restore
    decrypt <файл> [папка]    расшифровать копию на любом компьютере:
                              bot.sqlite3 и catalog.toml

Снимок делает штатный backup API SQLite: копия согласована, даже если бот
в этот момент пишет в базу, и включает изменения, которые ещё лежат в WAL.
Простое копирование файла bot.sqlite3 их бы потеряло.

Копия — tar.gz (bot.sqlite3 и catalog.toml), зашифрованный AES-256-GCM
ключом из пароля BACKUP_PASSWORD (scrypt). В копии UDID, Telegram ID
и переписка покупателей: на ПК и в облаке без пароля их не прочитать.
Без пароля копию не восстановить и нам — храните его отдельно от сервера.
"""
from __future__ import annotations

import getpass
import gzip
import io
import os
import re
import secrets
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import const
from .config import load_env_file

MAGIC = b"IAPKI-BACKUP-1\n"
SALT_SIZE = 16
NONCE_SIZE = 12

DB_NAME = "bot.sqlite3"
CATALOG_NAME = "catalog.toml"

# Имя копии — время по суткам команды (const.ORDER_DAY_TZ): так его и читать.
NAME_RE = re.compile(r"^iapki-(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.tar\.gz\.enc$")
NAME_FORMAT = "iapki-%Y-%m-%d_%H-%M-%S.tar.gz.enc"

# Правило хранения: все копии за 7 дней, по последней за каждую из 4 недель
# и за каждый из 6 месяцев — как --keep-within/--keep-weekly/--keep-monthly у restic.
KEEP_DAYS = 7
KEEP_WEEKS = 4
KEEP_MONTHS = 6


class BackupError(Exception):
    """Понятная человеку причина, по которой копия не сделана или не открыта."""


# ---------------------------------------------------------------- шифрование


def _key(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def encrypt(data: bytes, password: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = secrets.token_bytes(SALT_SIZE), secrets.token_bytes(NONCE_SIZE)
    # Заголовок входит в проверку подлинности: подменить его незаметно нельзя.
    return MAGIC + salt + nonce + AESGCM(_key(password, salt)).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes, password: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not blob.startswith(MAGIC):
        raise BackupError("Это не копия базы iApki")
    head = len(MAGIC)
    salt = blob[head:head + SALT_SIZE]
    nonce = blob[head + SALT_SIZE:head + SALT_SIZE + NONCE_SIZE]
    try:
        return AESGCM(_key(password, salt)).decrypt(nonce, blob[head + SALT_SIZE + NONCE_SIZE:], MAGIC)
    except InvalidTag:
        raise BackupError("Неверный пароль или файл копии повреждён") from None


# ------------------------------------------------------------------- снимок


def _check(path: Path) -> int:
    """integrity_check копии; возвращает число заказов — для отчёта."""
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise BackupError("Копия базы не прошла проверку целостности: %s" % result)
        try:
            return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        except sqlite3.OperationalError:
            raise BackupError("В копии нет таблицы заказов — это не база бота") from None
    finally:
        conn.close()


def snapshot(db_path: Path) -> tuple[bytes, int]:
    """Согласованный снимок живой базы: байты файла и число заказов в нём."""
    if not db_path.is_file():
        # sqlite3.connect создал бы пустую базу и мы бы «успешно» сохранили пустоту.
        raise BackupError("Базы %s нет — копировать нечего" % db_path)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / DB_NAME
        source = sqlite3.connect(db_path, timeout=30)
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
            # Копия — один самостоятельный файл, без WAL рядом: её можно открыть
            # где угодно. Бот при старте сам вернёт базе режим WAL.
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            target.close()
            source.close()
        orders = _check(copy)
        return copy.read_bytes(), orders


def pack(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def unpack(data: bytes) -> dict[str, bytes]:
    out = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            # Только наши два файла и только обычные файлы — никаких путей наружу.
            if member.isfile() and member.name in (DB_NAME, CATALOG_NAME):
                out[member.name] = tar.extractfile(member).read()
    if DB_NAME not in out:
        raise BackupError("В копии нет файла базы")
    return out


# ------------------------------------------------------------------ имена и хранение


def backup_name(moment: datetime) -> str:
    return moment.astimezone(const.ORDER_DAY_TZ).strftime(NAME_FORMAT)


def parse_name(name: str) -> datetime | None:
    found = NAME_RE.match(name)
    if not found:
        return None
    return datetime.strptime(found.group(1), "%Y-%m-%d_%H-%M-%S").replace(tzinfo=const.ORDER_DAY_TZ)


def to_keep(moments: list[datetime], now: datetime) -> set[datetime]:
    """Какие копии оставить: все за KEEP_DAYS дней и самую свежую из каждой
    из KEEP_WEEKS последних недель и KEEP_MONTHS последних месяцев, где копии есть."""
    keep = {m for m in moments if now - m <= timedelta(days=KEEP_DAYS)}
    for bucket, limit in (
        (lambda m: m.astimezone(const.ORDER_DAY_TZ).isocalendar()[:2], KEEP_WEEKS),
        (lambda m: (m.astimezone(const.ORDER_DAY_TZ).year, m.astimezone(const.ORDER_DAY_TZ).month), KEEP_MONTHS),
    ):
        seen: list = []
        for moment in sorted(moments, reverse=True):
            key = bucket(moment)
            if key in seen:
                continue
            if len(seen) == limit:
                break
            seen.append(key)
            keep.add(moment)
    return keep


def backups_in(folder: Path) -> dict[datetime, Path]:
    """Копии в папке по времени — по имени, а не по дате файла: на ПК она другая."""
    found = {}
    for path in folder.iterdir() if folder.is_dir() else ():
        moment = parse_name(path.name)
        if moment is not None and path.is_file():
            found[moment] = path
    return found


def prune(folder: Path, now: datetime) -> list[Path]:
    """Удаляет копии, которых нет в правиле хранения. Чужие файлы не трогает."""
    found = backups_in(folder)
    keep = to_keep(list(found), now)
    removed = []
    for moment, path in found.items():
        if moment not in keep:
            path.unlink()
            removed.append(path)
    return sorted(removed)


# ------------------------------------------------------------------- команды


def _password(ask: bool = False) -> str:
    password = os.getenv("BACKUP_PASSWORD") or ""
    if not password and ask and sys.stdin.isatty():
        password = getpass.getpass("Пароль копий (BACKUP_PASSWORD): ")
    if not password:
        raise BackupError(
            "BACKUP_PASSWORD не задан. Добавьте в .env длинный пароль (например, сгенерированный "
            "в Bitwarden) и сохраните его в Bitwarden: без него копии не восстановить."
        )
    if len(password) < 12:
        raise BackupError("BACKUP_PASSWORD короче 12 символов — для шифрования копий это слишком слабо")
    return password


def create(db_path: Path, folder: Path, catalog: Path | None, password: str,
           now: datetime | None = None) -> tuple[Path, int, list[Path]]:
    """Снимок → шифрованная копия в папке → удаление лишних. Возвращает копию,
    число заказов в ней и удалённые копии."""
    now = now or datetime.now(timezone.utc)
    data, orders = snapshot(db_path)
    files = {DB_NAME: data}
    if catalog is not None and catalog.is_file():
        files[CATALOG_NAME] = catalog.read_bytes()
    blob = encrypt(pack(files), password)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / backup_name(now)
    # Сначала во временный файл: оборванная запись не выглядит готовой копией.
    part = target.with_name("." + target.name + ".part")
    part.write_bytes(blob)
    os.chmod(part, 0o644)
    os.replace(part, target)
    return target, orders, prune(folder, now)


def restore(path: Path, db_path: Path, password: str) -> int:
    """Заменяет базу копией. Бот в это время должен быть остановлен."""
    files = unpack(decrypt(path.read_bytes(), password))
    fresh = db_path.with_name("." + DB_NAME + ".restore")
    fresh.write_bytes(files[DB_NAME])
    try:
        orders = _check(fresh)
    except BackupError:
        fresh.unlink()
        raise
    # WAL и shm от прежней базы к новой не относятся — без удаления SQLite
    # попытался бы «доиграть» их поверх восстановленной копии.
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    os.replace(fresh, db_path)
    return orders


def decrypt_to(path: Path, folder: Path, password: str) -> list[Path]:
    files = unpack(decrypt(path.read_bytes(), password))
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in files.items():
        target = folder / name
        target.write_bytes(data)
        written.append(target)
    return written


def _size(path: Path) -> str:
    size = path.stat().st_size
    if size < 1024 * 1024:
        return "%d КБ" % max(1, round(size / 1024))
    return "%.1f МБ" % (size / 1024 / 1024)


def main(argv: list[str]) -> int:
    load_env_file()
    db_path = Path(os.getenv("DB_PATH") or "/data/bot.sqlite3")
    folder = Path(os.getenv("BACKUP_DIR") or "/backups")
    ipa_dir = os.getenv("IPA_DIR")
    catalog = Path(ipa_dir) / CATALOG_NAME if ipa_dir else None
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if command == "create":
            target, orders, removed = create(db_path, folder, catalog, _password())
            print("Копия %s: %s, заказов %d" % (target.name, _size(target), orders))
            for path in removed:
                print("Удалена по правилу хранения: %s" % path.name)
        elif command == "list":
            found = backups_in(folder)
            for moment in sorted(found):
                print("%s  %s" % (found[moment].name, _size(found[moment])))
            print("Всего копий: %d" % len(found))
        elif command == "restore" and len(args) == 1:
            path = Path(args[0])
            if not path.is_absolute() and not path.exists():
                path = folder / path
            orders = restore(path, db_path, _password())
            print("База восстановлена из %s, заказов %d" % (path.name, orders))
        elif command == "decrypt" and len(args) in (1, 2):
            path = Path(args[0])
            out = Path(args[1]) if len(args) == 2 else path.with_name(path.name.split(".")[0])
            for written in decrypt_to(path, out, _password(ask=True)):
                print("Расшифровано: %s" % written)
        else:
            print(__doc__)
            return 2
    except BackupError as exc:
        print("Ошибка: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
