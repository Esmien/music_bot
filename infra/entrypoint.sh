#!/bin/sh
# Точка входа контейнера: запускает бота от непривилегированного пользователя.
set -e

# Если команда не передана через Docker или Compose, запускаем бота.
if [ "$#" -eq 0 ]; then
	set -- python -u bot.py
fi

# При запуске от root (по умолчанию в Docker) меняем владельца /data,
# куда смонтирован volume, на botuser, и переключаемся на него.
if [ "$(id -u)" = "0" ]; then
	chown botuser:botuser /data
	chown -R botuser:botuser /var/lib/lyria/audio

	# Переключаемся на botuser и запускаем команду с сохранением всех аргументов
	exec su -s /bin/sh botuser -c 'exec "$@"' -- sh "$@"
else
	# Запуск вне Docker или уже от обычного пользователя — права не трогаем.
	exec "$@"
fi
