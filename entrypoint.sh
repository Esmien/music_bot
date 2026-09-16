#!/bin/sh
set -e

# При запуске от root (по умолчанию в Docker) меняем владельца /data,
# куда смонтирован volume, на botuser, и переключаемся на него.
if [ "$(id -u)" = "0" ]; then
    chown botuser:botuser /data
    exec su botuser -s /bin/sh -c "python -u bot.py"
else
    exec python -u bot.py
fi
