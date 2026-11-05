#!/bin/sh
# Технические работы на сервере.
#
#   ./maintenance.sh on      копия базы, затем сайт и Mini App показывают страницу
#                            техработ, бот остановлен, в Telegram отвечает заглушка
#   ./maintenance.sh off     собирает бота из текущего кода, запускает вместо
#                            заглушки и, когда он подключился к Telegram,
#                            снимает страницу техработ
#   ./maintenance.sh status  что сейчас включено
#
# Обычной выкатке техработы не нужны: git pull && docker compose up -d --build
# собирает новый образ, пока старый бот работает, и меняет их за секунды.
# Пока техработы включены, возвращайте бота только командой off: обычный
# docker compose up запустит его рядом с заглушкой, и обоим Telegram ответит
# 409 Conflict.
set -eu
cd "$(dirname "$0")"

FLAG=maintenance/on

# Бот подключился к Telegram — эту строку main.py пишет после get_me.
bot_started() {
	docker compose logs --no-log-prefix --since "$1" bot 2>/dev/null | grep -q "запущен, режим оплаты"
}

case "${1:-}" in
on)
	# Перед работами — свежая копия базы: если что-то пойдёт не так, к ней можно вернуться.
	if ! sh ./backup.sh run; then
		echo "Копию сделать не удалось (причина выше) — техработы включаем без неё." >&2
	fi
	touch "$FLAG"
	echo "Страница техработ включена."
	docker compose stop bot
	docker compose --profile maintenance up -d maintenance
	echo "Бот остановлен, в Telegram отвечает заглушка."
	echo "Вносите изменения, потом: ./maintenance.sh off"
	;;
off)
	echo "Собираем бота из текущего кода, заглушка пока отвечает..."
	docker compose build bot
	start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
	docker compose --profile maintenance stop maintenance
	docker compose up -d bot
	echo "Ждём, пока бот подключится к Telegram..."
	waited=0
	until bot_started "$start"; do
		if [ "$waited" -ge 120 ]; then
			echo "Бот не подключился за 2 минуты — возвращаем заглушку, страница техработ остаётся." >&2
			docker compose stop bot
			docker compose --profile maintenance up -d maintenance
			echo "Причина — в логах: docker compose logs --tail 50 bot" >&2
			echo "Строку «Бот @… запущен» бот пишет при LOG_LEVEL=INFO; при WARNING её не будет." >&2
			exit 1
		fi
		sleep 2
		waited=$((waited + 2))
	done
	rm -f "$FLAG"
	echo "Бот работает, страница техработ снята."
	;;
status)
	if [ -f "$FLAG" ]; then
		echo "Страница техработ: включена"
	else
		echo "Страница техработ: выключена"
	fi
	docker compose --profile maintenance ps
	;;
*)
	echo "Использование: ./maintenance.sh on | off | status" >&2
	exit 2
	;;
esac
