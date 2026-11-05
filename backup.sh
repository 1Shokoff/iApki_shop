#!/bin/sh
# Резервные копии базы на сервере.
#
#   ./backup.sh                копия прямо сейчас
#   ./backup.sh list           какие копии есть
#   ./backup.sh restore ФАЙЛ   заменить базу копией: включает техработы (при этом
#                              делается копия текущей базы), меняет базу, возвращает бота
#   ./backup.sh install        один раз: расписание каждые 6 часов, пользователь
#                              iapki-backup для выгрузки копий на ПК, права на .env
#   ./backup.sh allow-pc 'ssh-ed25519 AAAA… iapki-pc'
#                              пустить ключ с ПК: только чтение папки копий по SFTP
#
# Копии лежат в backups/ рядом со скриптом и зашифрованы паролем BACKUP_PASSWORD
# из .env. Хранятся все за 7 дней, по одной за 4 недели и за 6 месяцев.
set -eu
cd "$(dirname "$0")"
DIR=$(pwd)
BACKUPS="$DIR/backups"
BACKUP_USER=iapki-backup
# Пользователь бота в образе (Dockerfile): копию пишет контейнер под ним.
APP_UID=10001

usage() {
	sed -n '2,11p' "$DIR/backup.sh" | sed 's/^# \{0,1\}//' >&2
	exit 2
}

prepare() {
	mkdir -p "$BACKUPS"
	chown "$APP_UID:$APP_UID" "$BACKUPS"
	# Читать копии может и iapki-backup: они зашифрованы.
	chmod 755 "$BACKUPS"
}

tool() {
	docker compose --profile tools run --rm -T backup "$@"
}

case "${1:-run}" in
run)
	echo "== $(date '+%Y-%m-%d %H:%M:%S')"
	prepare
	tool create
	;;
list)
	prepare
	tool list
	;;
restore)
	[ $# -eq 2 ] || usage
	name=$(basename "$2")
	if [ ! -f "$BACKUPS/$name" ]; then
		echo "Нет копии $BACKUPS/$name. Список: ./backup.sh list" >&2
		exit 1
	fi
	echo "Восстанавливаем базу из $name."
	# on сам делает копию текущей базы: если восстановили не то, к ней можно вернуться.
	sh ./maintenance.sh on
	tool restore "/backups/$name"
	sh ./maintenance.sh off
	;;
install)
	if [ "$(id -u)" -ne 0 ]; then
		echo "Нужен root." >&2
		exit 1
	fi
	if ! grep -q '^BACKUP_PASSWORD=.\{12,\}' .env; then
		echo "Сначала добавьте в .env строку BACKUP_PASSWORD=… (от 12 символов) и сохраните пароль в Bitwarden." >&2
		exit 1
	fi
	# docker compose подставляет переменные в значения .env: «$» в пароле молча
	# испортил бы его, и копии не открылись бы паролем из Bitwarden.
	if grep -q '^BACKUP_PASSWORD=.*[$"'"'"' ]' .env; then
		echo "В BACKUP_PASSWORD не должно быть «\$», кавычек и пробелов — возьмите буквы, цифры и дефисы." >&2
		exit 1
	fi
	if [ ! -d /etc/cron.d ]; then
		echo "Не нашёл cron. Установите: apt install cron" >&2
		exit 1
	fi
	# В .env токен бота и ключи: только root. Пользователь для выгрузки копий его не прочитает.
	chmod 600 .env
	prepare
	if ! id "$BACKUP_USER" >/dev/null 2>&1; then
		# Пароля нет совсем («*»), вход только по ключу из allow-pc.
		useradd --create-home --shell /bin/sh --password '*' "$BACKUP_USER"
		echo "Создан пользователь $BACKUP_USER."
	fi
	cat > /etc/cron.d/iapki-backup <<EOF
# Копия базы iApki каждые 6 часов — ./backup.sh install
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
17 */6 * * * root sh $DIR/backup.sh run >> /var/log/iapki-backup.log 2>&1
EOF
	chmod 644 /etc/cron.d/iapki-backup
	echo "Расписание: каждые 6 часов, в 17 минут. Журнал: /var/log/iapki-backup.log"
	echo "Первая копия — прямо сейчас:"
	sh "$DIR/backup.sh" run
	;;
allow-pc)
	[ $# -eq 2 ] || usage
	if ! id "$BACKUP_USER" >/dev/null 2>&1; then
		echo "Сначала: ./backup.sh install" >&2
		exit 1
	fi
	case "$2" in
	ssh-ed25519\ * | ssh-rsa\ * | ecdsa-sha2-*) ;;
	*)
		echo "Это не открытый ключ. Нужна одна строка из файла iapki_backup.pub." >&2
		exit 1
		;;
	esac
	sftp_server=""
	for path in /usr/lib/openssh/sftp-server /usr/libexec/openssh/sftp-server /usr/lib/ssh/sftp-server; do
		if [ -x "$path" ]; then
			sftp_server=$path
			break
		fi
	done
	if [ -z "$sftp_server" ]; then
		echo "Не нашёл sftp-server из OpenSSH." >&2
		exit 1
	fi
	home=$(getent passwd "$BACKUP_USER" | cut -d: -f6)
	keys="$home/.ssh/authorized_keys"
	mkdir -p "$home/.ssh"
	touch "$keys"
	key=$(echo "$2" | awk '{print $2}')
	if grep -qF "$key" "$keys"; then
		echo "Этот ключ уже добавлен."
	else
		# Ключ ПК умеет одно: читать папку копий по SFTP. Ни команд, ни записи, ни туннелей.
		echo "restrict,command=\"$sftp_server -R -d $BACKUPS\" $2" >> "$keys"
		echo "Ключ добавлен: $BACKUP_USER может только читать $BACKUPS по SFTP."
	fi
	chown -R "$BACKUP_USER:$BACKUP_USER" "$home/.ssh"
	chmod 700 "$home/.ssh"
	chmod 600 "$keys"
	;;
*)
	usage
	;;
esac
