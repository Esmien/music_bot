# Lyria Telegram Bot

Приватный Telegram-бот для генерации песен: пришлите стихи или заполните бриф — получите готовый MP3. Генерация выполняется через OpenRouter (модель Lyria), доступ — по личному ключу.

## Возможности

- 🎵 Генерация песни по готовому тексту или брифу: жанр, настроение, инструменты, темп, голос
- ✨ Обогащение промпта через LLM: идея превращается в детальный бриф с аппрувом и правками перед генерацией
- ⭐ Оценка и фидбек по готовой песне (сохраняются в БД)
- 🔐 Вход по ключу доступа: защита от перебора, сообщение с ключом автоматически удаляется из чата
- 💳 Остаток генераций по данным API OpenRouter
- 🧪 Демо-режим (`MOCK_MODE=1`) — весь сценарий без обращения к внешнему API
- 🐞 Отчёты о непойманных ошибках владельцу в Telegram

## Стек

- **Python 3.12**
- **aiogram 3** — бот-фреймворк: роутеры, FSM, клавиатуры
- **SQLAlchemy 2 (async)** — PostgreSQL (asyncpg), SQLite для тестов
- **Alembic** — миграции БД
- **Redis** — хранение FSM-состояний и служебных реестров
- **httpx** — запросы к OpenRouter, чтение SSE-потока
- **pydantic-settings** — конфигурация через `.env`
- **Pytest + pytest-asyncio** — юнит- и интеграционные тесты
- **Poetry** — управление зависимостями (`pyproject.toml` + `poetry.lock`)
- **Docker + docker compose** — развёртывание

## Структура проекта

~~~text
src/
├── bot.py               # точка входа: Bot, Dispatcher, регистрация роутеров, on_error
├── core/
│   ├── config.py        # конфигурация через pydantic Settings
│   ├── redis.py         # единый async-клиент Redis (FSM, реестры)
│   ├── database/
│   │   ├── engine.py    # async-движок, фабрика сессий, init_db()
│   │   └── models.py    # ORM-модели (User, GenerationFeedback)
│   └── utils/
│       ├── error_notify.py   # уведомления владельцу об ошибках
│       ├── exceptions.py     # кастомные исключения
│       └── stream_parser.py  # парсер SSE-потока OpenRouter
├── handlers/
│   ├── auth.py                 # /start, ввод ключа доступа, /logout, fallback
│   ├── base_handlers.py        # /cancel и отмена текущей операции
│   ├── credits_handlers.py     # /credits: остаток генераций
│   ├── filters.py              # кастомные фильтры (IsPendingAuth, NotCommand)
│   ├── enricher_handlers.py    # FSM-диалог обогащения промпта
│   ├── generation_handlers.py  # точка входа генерации, приём названия
│   └── generation_pipeline.py  # конвейер генерации: прогресс, отмена, сбои, отправка аудио
├── fsm/
│   ├── enricher_fsm.py         # состояния сценария обогащения промпта
│   ├── evaluation_fsm.py       # состояния оценки/фидбека
│   ├── generation_fsm.py       # состояния и лимиты диалога генерации
│   ├── generation_flags.py     # чистка «осиротевших» флагов после рестарта
│   └── registries/             # служебные реестры (auth_registry, task_registry)
├── keyboards/
│   ├── default_keyboards.py     # reply-клавиатуры
│   ├── enricher_keyboards.py    # inline-клавиатуры обогащения промпта
│   └── evaluation_keyboards.py  # inline-клавиатуры оценки и фидбека
├── services/
│   ├── enricher.py     # обогащение промпта через LLM, сохранение пары «исходный → обогащённый»
│   ├── generation.py   # запрос к OpenRouter (SSE) и мок-режим
│   └── pipeline.py     # оркестрация: пер-пользовательский лок, прогресс-бар, троттлинг
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
| `REDIS_HOST` / `REDIS_PORT` | — | `redis` / `6379` | Хост и порт Redis; в Docker-сети — `redis:6379` |
| `REDIS_URL` | — | `redis://localhost:6379/0` | Строка подключения к Redis (FSM-состояния); в Docker собирается из `REDIS_HOST`/`REDIS_PORT` |
| `MOCK_MODE` | — | `0` | `1` — демо-режим без вызова API |
| `MOCK_FILE` | — | — | JSON-мок с аудио в base64 для демо-режима |
| `ENRICH_URL` | — | — | URL чат-комплишн эндпоинта обогатителя (Open WebUI, OpenAI-совместимый API); пустое значение отключает обогащение |
| `ENRICH_TOKEN` | — | — | Токен доступа к обогатителю |
| `ENRICH_MODEL` | — | — | Модель обогатителя в терминах Open WebUI |

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
2. Нажмите «🎵 Сгенерировать», пришлите текст или идею.
3. Подтвердите обогащённый промпт или внесите правки.
4. Введите название — получите готовый MP3.
5. Оцените результат и оставьте фидбек.
