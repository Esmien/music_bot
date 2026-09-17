# Образ бота: тонкий Python-slim, запуск от непривилегированного пользователя.
FROM python:3.12-slim

WORKDIR /app

# Зависимости отдельным слоем: пока pyproject.toml и poetry.lock не меняются,
# Docker берёт слой из кэша и не переустанавливает пакеты
COPY pyproject.toml poetry.lock ./

# Poetry ставит пакеты прямо в системный site-packages, без виртуального
# окружения; --only main — без dev-зависимостей, --no-root — без установки
# самого пакета проекта
RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false \
    && poetry install --only main --no-root

COPY . .

# Непривилегированный пользователь: бот не должен работать от root.
# /data — точка монтирования volume с базой SQLite (см. docker-compose.yaml)
RUN useradd -m botuser \
    && mkdir /data \
    && chown botuser:botuser /data

# entrypoint копируется отдельно с явным chmod: бит исполнения
# не всегда сохраняется при COPY . . (зависит от прав файла в репозитории)
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
