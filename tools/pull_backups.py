"""Забирает копии базы с сервера на этот компьютер.

    python tools/pull_backups.py                  → в D:\\Backups\\iApki
    python tools/pull_backups.py --dir E:\\Копии   → в другую папку

Его запускает Планировщик Windows каждые 6 часов и при входе в систему
(tools/install_backup_task.ps1). Скачивает только новые копии и удаляет
лишние по тому же правилу хранения, что и на сервере. Журнал — pull.log
в папке копий.

На сервер ходит отдельным ключом ~/.ssh/iapki_backup под пользователем
iapki-backup: этому ключу сервер разрешает только читать папку копий
(./backup.sh allow-pc). Копии остаются зашифрованными; открыть:
python -m app.backup decrypt <файл>.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import backup  # noqa: E402

HOST = "iapki-backup@iapki.sytes.net"
KEY = Path.home() / ".ssh" / "iapki_backup"
FOLDER = Path("D:/Backups/iApki")

# Выполняет пакет команд sftp и возвращает их вывод.
Runner = Callable[[list[str]], str]


def sftp_runner(host: str, key: Path) -> Runner:
    def run(commands: list[str]) -> str:
        result = subprocess.run(
            # Только ключ из файла, без SSH-агента: агент Bitwarden спрашивал бы
            # подтверждение на каждый запуск, а Планировщик ответить не может.
            ["sftp", "-q", "-i", str(key), "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
             "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-b", "-", host],
            input="\n".join(commands) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            # Под Планировщиком без окна консоли.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "sftp завершился с кодом %d" % result.returncode)
        return result.stdout

    return run


def remote_names(listing: str) -> list[str]:
    """Имена копий из вывода «ls -1»; эхо команд sftp и чужие файлы отсекаются."""
    return sorted(name for name in (line.strip() for line in listing.splitlines()) if backup.parse_name(name))


def pull(run: Runner, folder: Path, now: datetime) -> tuple[list[str], list[Path]]:
    folder.mkdir(parents=True, exist_ok=True)
    missing = [name for name in remote_names(run(["ls -1"])) if not (folder / name).exists()]
    if missing:
        # Сначала во временные файлы: оборванная загрузка не выглядит готовой копией.
        run(['get "%s" "%s"' % (name, (folder / (name + ".part")).as_posix()) for name in missing])
        for name in missing:
            os.replace(folder / (name + ".part"), folder / name)
    return missing, backup.prune(folder, now)


def main() -> int:
    parser = argparse.ArgumentParser(description="Забирает копии базы iApki с сервера")
    parser.add_argument("--dir", type=Path, default=FOLDER, help="куда складывать копии (%(default)s)")
    parser.add_argument("--host", default=HOST, help="пользователь и сервер (%(default)s)")
    parser.add_argument("--key", type=Path, default=KEY, help="SSH-ключ (%(default)s)")
    args = parser.parse_args()

    args.dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        got, removed = pull(sftp_runner(args.host, args.key), args.dir, datetime.now(timezone.utc))
        line = "скачано %d, удалено старых %d" % (len(got), len(removed))
        code = 0
    except Exception as exc:  # журнал — единственное, что увидит владелец; пишем любую причину
        line = "ОШИБКА: %s" % exc
        code = 1
    with open(args.dir / "pull.log", "a", encoding="utf-8") as log:
        log.write("%s  %s\n" % (stamp, line))
    print(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
