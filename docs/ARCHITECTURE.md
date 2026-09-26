# Архитектура проекта

## System Design

Монолитное приложение с распределением кода по доменам.

- **Aiogram-бот** принимает сообщения и callback-запросы Telegram.
- **Обработчики доменов** управляют Telegram-взаимодействием и FSM-сценариями.
- **Сервисы доменов** содержат бизнес-логику и интеграции с API и БД.
- **PostgreSQL** хранит пользователей и данные о генерациях, оценках и отзывах.
- **Redis** используется для FSM-состояний и служебных реестров.
- **SQLite и fakeredis** применяются в тестах.

## Структура проекта

```text
.
├── src/
│   ├── bot.py                         # Инициализация бота, Dispatcher, Redis FSM и polling
│   ├── core/
│   │   ├── __init__.py                # Сборка доменных Router в единый Router
│   │   ├── config.py                  # Настройки окружения и UIConfig
│   │   ├── redis.py                   # Единый async-клиент Redis
│   │   ├── database/
│   │   │   ├── __init__.py            # Экспорт моделей, движка и SessionLocal
│   │   │   ├── engine.py              # Async SQLAlchemy engine, SessionLocal, init_db()
│   │   │   └── models.py              # Базовый класс Base и реэкспорт доменных моделей
│   │   └── utils/
│   │       ├── error_notify.py        # Логирование ошибок и уведомление владельца
│   │       └── exceptions.py          # Исключения конфигурации и доменов
│   └── domains/
│       ├── auth/
│       │   ├── handlers.py            # /logout, ввод ключа, фильтры авторизации и fallback
│       │   ├── service.py             # Проверка авторизации, ключ доступа и операции с Redis/БД
│       │   ├── registries/
│       │   │   └── auth_registry.py   # Операции с реестром ожидающих авторизацию в Redis
│       │   └── tests/
│       │       └── integration/
│       │           └── test_auth_flow.py
│       ├── base/
│       │   ├── handlers.py            # /start и /cancel
│       │   ├── keyboards.py           # Главное меню и клавиатура отмены
│       │   ├── models.py              # ORM-модель User
│       │   ├── service.py             # Получение названия последней генерации
│       │   └── tests/
│       ├── credits/
│       │   ├── handlers.py            # Команда и кнопка проверки кредитов
│       │   ├── service.py             # OpenRouter API и пересчёт баланса в генерации
│       │   └── tests/
│       │       └── integration/
│       │           └── test_credits.py
│       ├── enricher/
│       │   ├── handlers.py            # FSM-диалог идеи, обогащения, правок и подтверждения
│       │   ├── fsm.py                 # Состояния сценария обогащения
│       │   ├── keyboards.py           # Клавиатуры обогащения и выбора названия
│       │   ├── service.py             # API обогатителя, форматирование и сохранение результата
│       │   ├── validator.py            # Разбор и нормализация ответа обогатителя
│       │   └── tests/
│       ├── evaluation/
│       │   ├── handlers.py            # Приём оценки сгенерированной песни
│       │   ├── fsm.py                 # Совместимый реэкспорт FeedbackStates
│       │   ├── keyboards.py           # Клавиатура лайка и дизлайка
│       │   ├── service.py              # Операции сохранения оценки и отзыва
│       │   └── tests/
│       ├── feedback/
│       │   ├── handlers.py            # Выбор действия, приём отзыва и завершение сценария
│       │   ├── fsm.py                 # Состояния оценки и сбора отзыва
│       │   ├── keyboards.py           # Клавиатуры отправки отзыва и завершения
│       │   ├── models.py              # ORM-модель GenerationFeedback
│       │   ├── service.py             # Сохранение оценки и отзыва в GenerationFeedback
│       │   └── tests/
│       └── generation/
│           ├── handlers.py            # Запуск генерации, повтор и обработка названия
│           ├── pipeline_handlers.py   # Telegram-часть конвейера, FSM, прогресс и отправка аудио
│           ├── fsm.py                 # Состояния, ограничения длины и очистка FSM-флагов
│           ├── keyboards.py           # Клавиатура повтора генерации
│           ├── models.py              # ORM-модели Generation и GenerationStatus
│           ├── service.py              # Генерация через OpenRouter или MOCK_MODE, локи и прогресс
│           ├── registries/
│           │   └── task_registry.py   # Реестр активных задач генерации
│           └── tests/
├── infra/                             # Dockerfile, docker-compose и entrypoint
├── migrations/                        # Миграции Alembic
├── docs/                              # Документация проекта и задачи
├── pyproject.toml                     # Poetry, Ruff, Pytest и coverage
└── ARCHITECTURE.md
```

В каждом домене размещены его обработчики, сервисы, состояния, клавиатуры и тесты — если соответствующие компоненты нужны домену. Общая инфраструктура и ORM-модели находятся в `core`.

## Ключевые потоки

### 1. Запуск и авторизация

```text
Пользователь
  └─> domains/base/handlers.py: cmd_start
        ├─> domains/auth/service.py: is_authorized
        ├─> domains/base/service.py: get_last_generated_title
        └─> domains/auth/service.py: add_pending_auth
              └─> Redis: пользователь ожидает ключ доступа

Ввод ключа
  └─> domains/auth/handlers.py: handle_key
        ├─> удаление сообщения с ключом
        ├─> domains/auth/service.py: check_key_with_attempts
        ├─> domains/auth/service.py: mark_user_authorized
        │     └─> PostgreSQL: создание пользователя или обновление авторизации
        └─> удаление пользователя из pending_auth
```

`/logout` снимает авторизацию в БД, очищает FSM и отменяет активную задачу генерации, если она зарегистрирована.

### 2. Обогащение и генерация песни

```text
Пользователь
  └─> domains/generation/handlers.py: cmd_generate
        └─> domains/enricher/handlers.py: handle_idea
              ├─> domains/enricher/service.py: enrich_prompt
              │     └─> внешний OpenAI-совместимый API
              ├─> domains/enricher/validator.py: validate_enriched_prompt
              └─> подтверждение или правки результата
                    └─> domains/enricher/service.py: save_enriched_prompt
                          └─> PostgreSQL: Generation

Подтверждение и ввод названия
  └─> domains/generation/handlers.py
        └─> domains/generation/pipeline_handlers.py: generate_and_send
              ├─> domains/generation/service.py: user_generation_lock
              ├─> domains/generation/registries/task_registry.py
              ├─> domains/generation/service.py: run_generation
              │     ├─> MOCK_MODE: локальный mock-файл
              │     └─> OpenRouter: SSE-поток и сборка MP3 из base64-чанков
              ├─> Telegram: прогресс и отправка аудио
              └─> PostgreSQL: сохранение названия генерации
```

При ошибке генерации конвейер уведомляет владельца и показывает кнопку повтора. `/cancel` и `/logout` могут отменить задачу, зарегистрированную в `task_registry`.

### 3. Оценка и отзыв

```text
Отправленное аудио
  └─> domains/generation/pipeline_handlers.py
        └─> FSM: FeedbackStates.waiting_evaluation
              └─> domains/evaluation/handlers.py: handle_evaluate
                    ├─> сохранение лайка или дизлайка в FSM
                    └─> FSM: waiting_for_feedback_choice
                          └─> domains/feedback/handlers.py
                                ├─> сохранение оценки и/или текста отзыва
                                ├─> domains/feedback/service.py: save_feedback
                                └─> PostgreSQL: GenerationFeedback
```

Текстовый отзыв необязателен: пользователь может завершить сценарий после выставления оценки.

### 4. Проверка кредитов

```text
Пользователь
  └─> domains/credits/handlers.py: cmd_credits
        ├─> проверка авторизации
        ├─> domains/credits/service.py: get_credits_summary
        │     └─> OpenRouter API: баланс ключа
        └─> перевод долларов в примерное число генераций
```

Сбой сетевого запроса обрабатывается обработчиком кредитов: ошибка логируется, владелец уведомляется, пользователю отправляется сообщение о проблеме.

### 5. Обработка ошибок

```text
Ошибка в обработчике или сервисе
  └─> domains/.../handlers.py: локальная обработка, если предусмотрена
        └─> core/utils/error_notify.py: notify_owner

Необработанная ошибка
  └─> bot.py: on_error
        └─> core/utils/error_notify.py: notify_owner
```

`notify_owner` записывает ошибку в лог и отправляет владельцу traceback, если настроен `BOT_OWNER_ID`.

## Хранение состояния и данных

| Данные | Где хранятся | Назначение |
| --- | --- | --- |
| FSM-состояния и данные диалогов | Redis через `RedisStorage` Aiogram | Сохранение текущего этапа сценария между обновлениями и рестартами |
| Пользователи, прошедшие авторизацию | PostgreSQL, таблица `users` | Статус доступа пользователя |
| Ожидающие ввода ключа и счётчики попыток | Redis | Авторизация и защита от перебора ключа |
| Активные задачи генерации | Память процесса; набор user_id — Redis | Возможность отменить выполняющуюся задачу |
| Промпты и названия генераций | PostgreSQL, таблица `generations` | История генераций |
| Оценки и отзывы | PostgreSQL, таблица `generation_feedbacks` | Обратная связь о генерациях |
| Тестовые данные | In-memory SQLite и fakeredis | Изолированные тесты без боевых подключений |

Задачи `asyncio.Task` не переживают перезапуск процесса. При старте бот очищает реестр активных задач и вызывает `clear_orphaned_generation_flags()`, чтобы удалить оставшиеся в FSM флаги незавершённой генерации.
