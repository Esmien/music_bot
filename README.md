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
- **SQLAlchemy 2 + aiosqlite** — асинхронная работа с SQLite
- **httpx** — запросы к OpenRouter, чтение SSE-потока
- **python-dotenv** — конфигурация через `.env`
- **Pytest + pytest-asyncio** — юнит- и интеграционные тесты
- **Poetry** — управление зависимостями (`pyproject.toml` + `poetry.lock`)
- **Docker + docker compose** — развёртывание

## Структура проекта

~~~text
bot.py               # точка входа: Bot, Dispatcher, polling
config.py            # все настройки из переменных окружения
database/
├── engine.py        # async-движок, фабрика сессий, init_db
└── models.py        # ORM-модели (User)
handlers/
├── auth.py          # /start, ввод ключа доступа, /logout, fallback
├── generation.py    # FSM-сценарий генерации песни
├── generation_fsm.py       # FSM-состояния и лимиты диалога генерации
├── generation_pipeline.py  # конвейер генерации: локи, прогресс, отмена, сбои
├── credits.py       # остаток генераций
├── keyboards.py     # reply-клавиатуры
├── filters.py       # кастомные фильтры
├── state.py         # общее состояние между хендлерами
└── utils.py         # уведомления владельцу об ошибках
services/
└── generation.py    # запрос к OpenRouter (SSE) и мок-режим
tests/               # юнит- и интеграционные тесты
pyproject.toml · poetry.lock · Dockerfile · docker-compose.yaml · entrypoint.sh
~~~

## Переменные окружения

Скопируйте `.env.example` в `.env` и заполните:

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | Токен бота от @BotFather |
| `OPENROUTER_API_KEY` | ✅ | — | Ключ API OpenRouter |
| `BOT_ACCESS_KEY` | ✅ | — | Ключ, который пользователь присылает боту для входа |
| `SONG_PRICE` | ✅ | — | Стоимость одной генерации, $ — для расчёта остатка песен |
| `MODEL_ID` | — | `google/lyria-3-pro-preview` | Модель OpenRouter |
| `BOT_OWNER_ID` | — | `0` | Telegram ID владельца: ему уходят отчёты об ошибках |
| `DATABASE_URL` | — | `sqlite+aiosqlite:///./bot.db` | Строка подключения к БД (в Docker переопределяется) |
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

В логах должна появиться строка `Starting bot`. Таблицы БД создаются автоматически при первом запуске. Остановка: `docker compose down`. База SQLite хранится в именованном томе `db_data` и переживает пересоздание контейнера.

## Локальный запуск (без Docker)

~~~bash
poetry install
poetry run python bot.py
~~~

## Тесты

~~~bash
poetry run pytest
~~~

## Как пользоваться

1. `/start` → отправьте боту ключ доступа.
2. Нажмите «🎵 Сгенерировать», пришлите текст или заполненный бриф.
3. Введите название — получите готовый MP3.

