"""Конфигурация проекта: чтение переменных окружения.

Все настройки собираются здесь в одном месте — остальные модули
импортируют только этот файл, ничего не читая из окружения напрямую.
"""

import os

from dotenv import load_dotenv

# Подхватываем .env из корня проекта при локальном запуске;
# в Docker переменные приходят через environment/docker-compose
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL_ID = os.getenv("MODEL_ID", "google/lyria-3-pro-preview")
BOT_ACCESS_KEY = os.getenv("BOT_ACCESS_KEY", "")
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0"))

# Fail fast: без цены генерации расчёт остатков песен невозможен
_song_price = os.getenv("SONG_PRICE")
if not _song_price:
    raise RuntimeError("Переменная окружения SONG_PRICE не задана")
SONG_PRICE = float(_song_price)

MOCK_MODE = os.getenv("MOCK_MODE", "0") == "1"
MOCK_FILE = os.getenv("MOCK_FILE", "")

# PostgreSQL; asyncpg — асинхронный драйвер, обязательный для SQLAlchemy в async-режиме.
# В Docker переопределяется через docker-compose, aiosqlite остаётся для локальных тестов
POSTGRES_USER=os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD=os.getenv("POSTGRES_PASSWORD")
POSTGRES_HOST=os.getenv("POSTGRES_HOST")
POSTGRES_PORT=os.getenv("POSTGRES_PORT")
POSTGRES_NAME=os.getenv("POSTGRES_DB")

DATABASE_URL=f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_NAME}"

# Redis: хранение FSM-состояний (переживают рестарт контейнера)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
