# Lyria Telegram Bot

Приватный Telegram-бот для генерации песен: пришлите стихи или заполните бриф — получите готовый MP3. Генерация выполняется через OpenRouter (модель Lyria), доступ — по личному ключу.

## Возможности

- 🎵 Генерация песни по готовому тексту или брифу: жанр, настроение, инструменты, темп, голос
- 🔐 Вход по ключу доступа: защита от перебора, сообщение с ключом автоматически удаляется из чата
- 💳 Остаток генераций по данным API OpenRouter
- 🧪 Демо-режим (`MOCK_MODE=1`) — весь сценарий без обращения к внешнему API
- 🐞 Отчёты о непойманных ошибках владельцу в Telegram

## Стек

- **Python 3.12**
- **aiogram 3** — бот-фреймворк: роутеры, FSM, клавиатуры
- **SQLAlchemy 2 (async)** — PostgreSQL (asyncpg), SQLite для тестов
- **Alembic** — миграции БД
- **Redis** — хранение FSM-состояний
- **httpx** — запросы к OpenRouter, чтение SSE-потока
- **pydantic-settings** — конфигурация через `.env`
- **Pytest + pytest-asyncio** — юнит- и интеграционные тесты
- **Poetry** — управление зависимостями (`pyproject.toml` + `poetry.lock`)
- **Docker + docker compose** — развёртывание

## Структура проекта

~~~text
src/
├── bot.py               # точка входа: Bot, Dispatcher, polling
├── config.py            # конфигурация через pydantic Settings
├── database/
│   ├── engine.py        # async-движок, фабрика сессий
│   └── models.py        # ORM-модели (User, GenerationFeedback)
├── handlers/
│   ├── auth.py             # /start, ввод ключа доступа, /logout, fallback
│   ├── generation.py       # FSM-сценарий генерации песни
│   ├── generation_fsm.py   # FSM-состояния и лимиты диалога генерации
│   ├── generation_pipeline.py  # конвейер генерации: локи, прогресс, отмена, сбои
│   ├── credits.py          # остаток генераций
│   ├── filters.py          # кастомные фильтры
│   └── state.py            # Redis-реестры и общее состояние между хендлерами
├── keyboards/
│   └── default_keyboards.py    # reply-клавиатуры
├── services/
│   └── generation.py    # запрос к OpenRouter (SSE) и мок-режим
└── utils/
    ├── error_notify.py  # уведомления владельцу об ошибках
    ├── exceptions.py    # кастомные исключения
    └── stream_parser.py # парсер SSE-потока OpenRouter
migrations/             # миграции Alembic
tests/                  # юнит- и интеграционные тесты
pyproject.toml · poetry.lock · infra/Dockerfile · infra/docker-compose.yml · infra/entrypoint.sh · .github/workflows (CI/CD)
~~~

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните:

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | Токен бота от @BotFather |
| `OPENROUTER_API_KEY` | ✅ | — | Ключ API OpenRouter |
| `BOT_ACCESS_KEY` | ✅ | — | Ключ, который пользователь присылает боту для входа |
| `SONG_PRICE` | ✅ | — | Стоимость одной генерации, $ — для расчёта остатка песен |
| `COMPOSE_FILE` / `COMPOSE_PROJECT_NAME` | — | — | Путь к docker-compose.yml и имя проекта для compose |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | ✅ | — | Пользователь, пароль и имя БД PostgreSQL |
| `POSTGRES_HOST` / `POSTGRES_PORT` | ✅ | — | Хост и порт PostgreSQL (в Docker-сети — `postgres:5432`) |
| `MODEL_ID` | — | `google/lyria-3-pro-preview` | Модель OpenRouter |
| `BOT_OWNER_ID` | — | `0` | Telegram ID владельца: ему уходят отчёты об ошибках |
| `REDIS_URL` | — | `redis://localhost:6379/0` | Строка подключения к Redis (FSM-состояния); в Docker собирается из `REDIS_HOST`/`REDIS_PORT` |
| `MOCK_MODE` | — | `0` | `1` — демо-режим без вызова API |
| `MOCK_FILE` | — | — | JSON-мок с аудио в base64 для демо-режима |

## Развёртывание в Docker

1. Создайте и заполните `.env`:

~~~bash
cp .env.example .env
~~~

2. Соберите и запустите контейнер:

~~~bash
docker compose up -d --build
~~~

3. Проверьте, что бот стартовал:

~~~bash
docker compose logs -f
~~~

В логах должна появиться строка `Starting bot`. Схема БД применяется миграциями Alembic при запуске. Остановка: `docker compose down`. Данные PostgreSQL и Redis хранятся в именованных томах и переживают пересоздание контейнеров.

## CI/CD

GitHub Actions: `.github/workflows/ci.yml` — Ruff и Pytest на каждый push/PR; `.github/workflows/deploy.yml` — после зелёного CI в `main` деплой на VPS по SSH (`git pull` + `docker compose up -d --build`). Требуются секреты репозитория: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PROJECT_DIR`.

## Локальный запуск (без Docker)

~~~bash
poetry install
poetry run python src/bot.py
~~~

## Тесты

~~~bash
poetry run pytest
~~~

## Как пользоваться

1. `/start` → отправьте боту ключ доступа.
2. Нажмите «🎵 Сгенерировать», пришлите текст или заполненный бриф.
3. Введите название — получите готовый MP3.

