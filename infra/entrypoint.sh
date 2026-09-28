#!/bin/sh
# Точка входа контейнера: запускает бота от непривилегированного пользователя.
set -e

# Если команда не передана через Docker или Compose, запускаем бота.
if [ "$#" -eq 0 ]; then
	set -- python -u bot.py
	run_migrations=1
else
	run_migrations=0
fi

# При запуске от root (по умолчанию в Docker) меняем владельца /data,
# куда смонтирован volume, на botuser, и переключаемся на него.
if [ "$(id -u)" = "0" ]; then
	chown botuser:botuser /data

	# Миграции запускаются только в контейнере бота; воркеры используют
	# ту же БД, но не должны запускать Alembic при каждом старте.
	if [ "$run_migrations" -eq 1 ]; then
		su -s /bin/sh botuser -c 'alembic upgrade head'
	fi
	
	# Переключаемся на botuser и запускаем команду с сохранением всех аргументов
	exec su -s /bin/sh botuser -c 'exec "$@"' -- sh "$@"
else
	# Запуск вне Docker или уже от обычного пользователя — права не трогаем.
	if [ "$run_migrations" -eq 1 ]; then
		alembic upgrade head
	fi
	exec "$@"
fi
