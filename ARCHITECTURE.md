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
│   │   ├── config.py       #   pydantic Settings: константы, флаги; единственная точка
│   │   │                   #   чтения окружения, UIConfig с текстами кнопок
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
│   │   ├── credits.py             # /credits: остаток генераций через API OpenRouter
│   │   ├── filters.py             # кастомные фильтры (IsPendingAuth, NotCommand)
│   │   ├── generation.py          # FSM-диалог генерации: кнопка «Сгенерировать», ввод промпта и названия, повтор
│   │   └── generation_pipeline.py # движок генерации: пер-пользовательский лок, прогресс-бар,
│   │                              #   отмена и обработка сбоев, отправка аудио (не знает о роутере)
│   ├── fsm/                # Состояния пользователя (на каком этапе он находится)
│   │   ├── generation_fsm.py      # состояния и лимиты диалога генерации
│   │   └── evaluation_fsm.py      # состояния оценки/фидбека и обогащения промпта; здесь же
│   │                              #   реестры pending_auth и active_tasks (живые задачи генерации),
│   │                              #   чистка «осиротевших» флагов generating после рестарта
│   ├── keyboards/          # Клавиатуры для бота (для различных сценариев)
│   │   ├── default_keyboards.py   # главная reply-клавиатура и кнопка отмены
│   │   └── enricher_keyboards.py  # inline-клавиатуры обогащения промпта и фидбека
│   │                              #   (callback_data по схеме "<домен>:<действие>")
│   ├── services/           # Бизнес-логика, движок
│   │   └── generation.py          # генерация песни: реальный вызов OpenRouter (SSE-стрим,
│   │                              #   сборка аудио из base64-чанков) и демо-режим MOCK_MODE
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
  └─> handlers/generation.py                 (FSM: промпт -> название)
        └─> generation_pipeline.generate_and_send   (фоновая задача)
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
| FSM-данные пользователей | Redis | Переживают рестарт контейнера |
| `pending_auth` (ожидающие ввод ключа) | Redis | Множество |
| `active_tasks` (живые задачи генерации) | Память процесса | `asyncio.Task` не сериализуется; след в FSM чистит `clear_orphaned_generation_flags()` на старте бота |
| Бизнес-данные (пользователи, фидбек по генерациям) | PostgreSQL | — |
| Тестовое окружение | SQLite (in-memory) + fakeredis | Заменяют PostgreSQL и Redis в тестах |
