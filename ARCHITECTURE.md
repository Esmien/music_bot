# Архитектура проекта

## System Design

Монолит с распределением по доменам.

- **Aiogram-бот** принимает сообщения.
- **Хендлеры** управляют FSM-диалогом и валидируют ввод.
- **Бизнес-логика** изолирована в `services`.
- **Результаты** сохраняются в PostgreSQL через SQLAlchemy (async).
- **FSM-состояния** и служебные реестры хранятся в Redis и переживают рестарт.

---

## Структура проекта

```text
.
├── .github/                # GitHub Actions
├── src/                    # Исходный код бота
│   ├── core/               # Инфраструктурное ядро: конфигурация и общие подключения
│   │   ├── config.py       #   pydantic Settings: сгруппированные конфиги (BotConfig,
│   │   │                   #   EnrichPromptConfig, GenerationConfig, DatabaseConfig,
│   │   │                   #   RedisConfig), UIConfig с текстами кнопок;
│   │   │                   #   единственная точка чтения окружения
│   │   ├── redis.py        #   единый async-клиент Redis для FSM-хранилища и реестров
│   │   ├── database/       #   модули для работы с БД (модели, подключения, фабрики)
│   │   │   ├── engine.py   #     async-движок, фабрика сессий SessionLocal, init_db()
│   │   │   └── models.py   #     ORM-модели (User, GenerationFeedback)
│   │   └── utils/          #   общие утилиты без бизнес-логики
│   │       ├── error_notify.py   # notify_owner: лог + traceback владельцу в Telegram
│   │       ├── exceptions.py     # доменные исключения (APINotSet, AccessKeyNotSet)
│   │       └── stream_parser.py  # парсер SSE-потока OpenRouter
│   ├── handlers/           # Все обработчики команд Telegram
│   │   ├── auth.py                # /start, вход по ключу (защита от перебора), /logout, fallback
│   │   ├── base_handlers.py       # команда /cancel и отмена текущей операции
│   │   ├── credits_handlers.py    # /credits: остаток генераций через API OpenRouter
│   │   ├── filters.py             # кастомные фильтры (IsPendingAuth, NotCommand)
│   │   ├── enricher_handlers.py   # FSM-диалог обогащения промпта: идея -> обогащение
│   │   │                          #   -> аппрув/правки -> запрос названия
│   │   ├── generation_handlers.py # точка входа генерации (кнопка «Сгенерировать»),
│   │   │                          #   приём названия песни, повтор после сбоя
│   │   └── generation_pipeline.py # конвейер генерации: FSM-флаги, отрисовка прогресса
│   │                              #   через edit_text, отмена и обработка сбоев,
│   │                              #   отправка аудио (не знает о роутере)
│   ├── fsm/                # Состояния пользователя (на каком этапе он находится)
│   │   ├── enricher_fsm.py        # состояния сценария обогащения промпта
│   │   ├── evaluation_fsm.py      # состояния оценки/фидбека после генерации
│   │   ├── generation_fsm.py      # состояния и лимиты диалога генерации
│   │   ├── generation_flags.py    # чистка «осиротевших» флагов generating после рестарта
│   │   └── registries/            # служебные реестры
│   │       ├── auth_registry.py   #   pending_auth (ожидающие ввод ключа), Redis
│   │       └── task_registry.py   #   active_tasks (живые задачи генерации), память процесса
│   ├── keyboards/          # Клавиатуры для бота (для различных сценариев)
│   │   ├── default_keyboards.py   # главная reply-клавиатура и кнопка отмены
│   │   ├── enricher_keyboards.py  # inline-клавиатуры обогащения промпта
│   │   │                          #   (callback_data по схеме "<домен>:<действие>")
│   │   └── evaluation_keyboards.py # inline-клавиатуры оценки и фидбека
│   ├── services/           # Бизнес-логика, движок
│   │   ├── enricher.py            # обогащение промпта через LLM: запрос, форматирование
│   │   │                          #   ответа, сохранение пары «исходный → обогащённый»
│   │   ├── generation.py          # генерация песни: реальный вызов OpenRouter (SSE-стрим,
│   │   │                          #   сборка аудио из base64-чанков) и демо-режим MOCK_MODE
│   │   └── pipeline.py            # оркестрация генерации: пер-пользовательский лок,
│   │                              #   сборка текста прогресс-бара и троттлинг правок
│   │                              #   статуса (лимиты Telegram)
│   └── bot.py              # Точка входа: бот, диспетчер, регистрация роутеров,
│                           #   хранилище FSM, глобальный хэндлер on_error
├── tests/                  # Тесты
│   ├── conftest.py         # общие фикстуры: in-memory SQLite, fakeredis, фабрики сообщений
│   │                       #   и FSM-заглушек; окружение задаётся до импортов проекта
│   ├── unit/               # юнит-тесты сервисов и утилит (внешние API мокаются)
│   └── integration/        # интеграционные тесты хендлеров с реальной in-memory БД
├── infra/                  # Инфраструктурные файлы (Docker, entrypoint.sh)
├── migrations/             # Миграции Alembic, генерируются автоматически
├── docs/                   # Документы: road map, планы, канбан-доска
├── poetry.lock             # Зависимости проекта
├── pyproject.toml          # Конфигурация проекта (Ruff, Pytest, coverage)
├── .dockerignore           # Исключения из Docker-образа
├── .gitignore              # Исключения git
└── .env.example            # Шаблон переменных окружения
```

---

## Ключевые потоки

### 1. Генерация песни

```text
Пользователь
  └─> handlers/generation_handlers.py        (кнопка «🎵 Сгенерировать»)
        └─> handlers/enricher_handlers.py    (FSM: идея -> обогащение -> аппрув/правки)
              └─> services/enricher.py       (обогащение промпта через LLM, сохранение в БД)
        └─> handle_title                     (ввод названия после аппрува)
              └─> generation_pipeline.generate_and_send   (фоновая задача)
                    └─> services/pipeline.py       (пер-пользовательский лок, троттлинг прогресса)
                          └─> services/generation.py     (OpenRouter или MOCK_MODE, прогресс через колбэк)
                                └─> отправка аудио, очистка FSM
```

> **Отмена** — через `/cancel` или `/logout` (`task.cancel()` по `active_tasks`).

### 2. Обработка ошибок

```text
Необработанное исключение
  └─> глобальный хэндлер on_error (bot.py)
        └─> core/utils/error_notify.notify_owner   (лог + сообщение владельцу)
```

### 3. Конфигурация

```text
Переменные окружения ──> core/config.py   (pydantic Settings, fail fast на обязательных значениях)
```

---

## Хранение состояния

| Данные | Где хранится | Примечание |
| --- | --- | --- |
| FSM-данных пользователей | Redis | Переживают рестарт контейнера |
| `pending_auth` (ожидающие ввод ключа) | Redis | Реестр `src/fsm/registries/auth_registry.py` |
| `active_tasks` (живые задачи генерации) | Память процесса | `asyncio.Task` не сериализуется; след в FSM чистит `clear_orphaned_generation_flags()` (`src/fsm/generation_flags.py`) на старте бота |
| Бизнес-данные (пользователи, фидбек по генерациям) | PostgreSQL | — |
| Тестовое окружение | SQLite (in-memory) + fakeredis | Заменяют PostgreSQL и Redis в тестах |
