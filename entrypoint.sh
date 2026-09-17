#!/bin/sh
# Точка входа контейнера: запускает бота от непривилегированного пользователя.
set -e

# При запуске от root (по умолчанию в Docker) меняем владельца /data,
# куда смонтирован volume, на botuser, и переключаемся на него.
# -u у python — небуферизованный вывод, иначе docker logs отстаёт
if [ "$(id -u)" = "0" ]; then
    chown botuser:botuser /data
    # exec подменяет shell процессом python: SIGTERM при docker compose stop
    # доходит до бота напрямую, и контейнер останавливается быстро
    exec su botuser -s /bin/sh -c "python -u bot.py"
else
    # Запуск вне Docker или уже от обычного пользователя — права не трогаем
    exec python -u bot.py
fi
