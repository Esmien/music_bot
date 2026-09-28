FROM python:3.12-slim

WORKDIR /app

# Контекст сборки — корень проекта, поэтому ../ больше не нужен
COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry==2.4.1 --only-binary=:all: \
    && poetry config virtualenvs.create false \
    && poetry install --only main --no-root


RUN useradd -m botuser \
    && mkdir /data \
    && chown botuser:botuser /data

# Копируем исходники из корня проекта
COPY src/ .
# Миграции Alembic
COPY alembic.ini ./
COPY migrations/ migrations/
# Забираем скрипт из папки infra
COPY infra/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
