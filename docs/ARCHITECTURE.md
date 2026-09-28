# Архитектура проекта

## System Design

Событийно-ориентированное приложение с распределением кода по доменам и асинхронной обработкой задач.

- **Aiogram-бот** принимает сообщения и callback-запросы Telegram.
- **Обработчики доменов** управляют Telegram-взаимодействием, FSM-сценариями и публикуют события в брокер.
- **Воркеры доменов** слушают события из брокера и выполняют фоновые задачи.
- **Сервисы доменов** содержат бизнес-логику и интеграции с внешними API и БД.
- **RabbitMQ** используется как брокер сообщений для TaskIQ.
- **TaskIQ** обеспечивает асинхронную обработку задач и событийную связь между компонентами.
- **PostgreSQL** хранит пользователей и данные о генерациях, оценках и отзывах.
- **Redis** используется для FSM-состояний и служебных реестров.
- **SQLite, fakeredis и InMemoryBroker** применяются в тестах.

## Структура проекта

```text
.
├── src/
│   ├── bot.py                         # Инициализация бота, Dispatcher, Redis FSM и webhook/polling
│   ├── core/
│   │   ├── __init__.py                # Сборка доменных Router в единый Router и регистрация воркеров
│   │   ├── broker.py                  # Инициализация TaskIQ брокера (RabbitMQ или InMemory)
│   │   ├── lifecycle.py               # Управление жизненным циклом приложения и graceful shutdown
│   │   ├── config.py                  # Настройки окружения и UIConfig
│   │   ├── redis.py                   # Единый async-клиент Redis и ключи для реестров
│   │   ├── task_registry.py           # Глобальный реестр активных генераций в Redis
│   │   │   ├── __init__.py            # Экспорт моделей, движка и SessionLocal
│   │   │   ├── engine.py              # Async SQLAlchemy engine, get_session, init_db()
│   │   │   ├── __init__.py            # Реэкспорт engine, get_session и моделей
│   │   │   └── models.py              # Базовый класс Base
│   │   └── utils/
│   │       ├── error_notify.py        # Логирование ошибок и уведомление владельца
│   │       ├── exceptions.py          # Исключения конфигурации и доменов
│   │       └── fsm_helpers.py         # Вспомогательные функции для работы с FSM
│   └── domains/
│       ├── shared/
│       │   ├── contracts.py           # Реэкспорт контрактов команд и событий
│       │   └── ports.py               # Реэкспорт портов Telegram
│       ├── shared/
│       │   ├── contracts.py           # Реэкспорт контрактов команд и событий
│       │   ├── auth_messages.py       # Текстовые сообщения домена авторизации
│       │   ├── registries/
│       │   │   └── auth_registry.py   # Операции с реестром ожидающих авторизацию в Redis
│       │   └── tests/
│       │       └── unit/
│       │           └── test_auth_flow.py  # Интеграционные тесты авторизации
│       │   ├── base_messages.py       # Текстовые сообщения базового домена
│       │   ├── handlers.py            # /start и /cancel
│       │   ├── keyboards.py           # Главное меню и клавиатура отмены
│       │   ├── models.py              # ORM-модель User
│       │   ├── service.py             # Получение названия последней генерации
│       │   └── tests/
│       │   ├── credits_messages.py    # Текстовые сообщения домена кредитов
│       │   ├── handlers.py            # Команда и кнопка проверки кредитов
│       │   ├── service.py             # OpenRouter API и пересчёт баланса в генерации
│       │   └── tests/
│       │       └── unit/
│       │           ├── test_credits.py        # Интеграционные тесты сервиса кредитов
│       │           └── test_credits_worker.py # Интеграционные тесты воркера кредитов
│       │   └── worker.py              # TaskIQ-воркер для проверки кредитов
│       ├── enricher/
│       │   ├── handlers.py            # FSM-диалог идеи, обогащения, правок и подтверждения
│       │   ├── fsm.py                 # Состояния сценария обогащения
│       │   ├── keyboards.py           # Клавиатуры обогащения и выбора названия
│       │   ├── service.py             # API обогатителя, валидация и сохранение результата
│       │   ├── validator.py            # Разбор и нормализация ответа обогатителя
│       │   └── tests/
│       ├── evaluation/
│       │   ├── handlers.py            # Приём оценки и публикация события EvaluationCompleted
│       │   ├── fsm.py                 # Реэкспорт FeedbackStates для совместимости
│       │   ├── keyboards.py           # Клавиатура оценки (лайк/дизлайк)
│       │   ├── service.py             # Сохранение оценки в БД
│       │   └── tests/
│       ├── feedback/
│       │   ├── handlers.py            # Выбор действия после оценки, приём отзыва и завершение
│       │   ├── fsm.py                 # Состояния оценки и сбора отзыва
│       │   ├── keyboards.py           # Клавиатуры выбора действия и отправки отзыва
│       │   ├── models.py              # ORM-модель GenerationFeedback (оценки и отзывы)
│       │   ├── service.py             # Сохранение текстовых отзывов в БД
│       │   └── tests/
│       └── generation/
│           ├── handlers.py            # Запуск генерации, повтор и обработка названия
│           ├── pipeline_handlers.py   # Telegram-часть конвейера, FSM, прогресс и отправка аудио
│           ├── fsm.py                 # Состояния, ограничения длины и очистка FSM-флагов
│           ├── keyboards.py           # Клавиатура повтора генерации
│           ├── models.py              # ORM-модель Generation и enum GenerationStatus
│           ├── service.py              # Генерация через OpenRouter или MOCK_MODE, локи и прогресс
│           ├── registries/
│           │   └── task_registry.py   # Доменный реестр активных задач генерации (legacy)
│           └── tests/
├── infra/                             # Dockerfile, docker-compose и entrypoint
├── migrations/                        # Миграции Alembic
├── docs/                              # Документация проекта и задачи
├── pyproject.toml                     # Poetry, Ruff, Pytest и coverage
└── ARCHITECTURE.md
```

В каждом домене размещены его обработчики, сервисы, состояния, клавиатуры и тесты — если соответствующие компоненты нужны домену. Общая инфраструктура и ORM-модели находятся в `core`.

## Ключевые потоки

### 1. Запуск, авторизация и проверка кредитов

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

`/credits` или кнопка проверки кредитов публикует команду `CheckCreditsCommand` в брокер:

```text
Пользователь
  └─> domains/credits/handlers.py: cmd_credits или callback
        └─> публикация CheckCreditsCommand в TaskIQ
              └─> domains/credits/worker.py: check_credits_handler
                    ├─> domains/credits/service.py: get_credits_summary
                    │     └─> OpenRouter API: баланс ключа
                    ├─> перевод долларов в примерное число генераций
                    └─> отправка результата через TelegramPort
```

Воркер обрабатывает команду асинхронно и отправляет результат пользователю. При ошибке уведомляет владельца.

### 2. Обогащение промпта (событийный флоу)

```text
Пользователь вводит идею

```text
Пользователь
  └─> domains/enricher/handlers.py: handle_idea
        ├─> сохранение идеи в FSM
        ├─> публикация EnrichPromptCommand в TaskIQ
        └─> FSM: PromptEnricherStates.waiting_enrichment
              └─> domains/enricher/worker.py: enrich_prompt_handler
                    ├─> domains/enricher/service.py: enrich_prompt
                    │     └─> внешний OpenAI-совместимый API
                    ├─> domains/enricher/validator.py: validate_enriched_prompt
                    ├─> domains/enricher/service.py: save_enriched_prompt
                    │     └─> PostgreSQL: создание Generation
                    ├─> отправка обогащённого промпта через TelegramPort
                    └─> публикация PromptEnriched события

Пользователь подтверждает промпт
  └─> domains/enricher/handlers.py: handle_enriched_prompt_confirmation
        ├─> FSM: GenerationStates.waiting_title
        └─> запрос названия трека

Пользователь вводит название
  └─> domains/generation/handlers.py: handle_title_input
        ├─> сохранение названия в FSM
        ├─> публикация StartGenerationCommand в TaskIQ
        └─> FSM: GenerationStates.generating
```

### 3. Генерация музыки (событийный флоу)

```text
Воркер получает команду StartGenerationCommand
  └─> domains/generation/worker.py: start_generation_handler
        ├─> получение данных генерации из БД
        ├─> domains/generation/service.py: user_generation_lock
              ├─> domains/generation/registries/task_registry.py
              ├─> domains/generation/service.py: run_generation
        │     ├─> MOCK_MODE: локальный mock-файл
        │     └─> OpenRouter: SSE-поток и сборка MP3 из base64-чанков
        ├─> отправка прогресса и аудио через TelegramPort
        ├─> PostgreSQL: обновление статуса генерации
        └─> публикация GenerationSucceeded или GenerationFailed события
```

При ошибке генерации воркер уведомляет владельца, публикует событие `GenerationFailed` и показывает кнопку повтора.

`/cancel` и `/logout` могут отменить задачу через Redis-флаг, который проверяется воркером генерации.

### 4. Оценка и отзыв (событийный флоу)

После успешной генерации срабатывает воркер оценки:

```text
Событие GenerationSucceeded
  └─> domains/evaluation/worker.py: request_evaluation_handler
        ├─> FSM: FeedbackStates.waiting_evaluation
        ├─> отправка запроса оценки через TelegramPort
        └─> отправка клавиатуры оценки

Пользователь ставит оценку
  └─> domains/evaluation/handlers.py: handle_evaluate
        ├─> domains/evaluation/service.py: save_evaluation
        │     └─> PostgreSQL: создание GenerationFeedback с оценкой
        ├─> публикация EvaluationCompleted события
        └─> FSM: FeedbackStates.waiting_for_feedback_choice

Пользователь выбирает действие (оставить отзыв или пропустить)
  └─> domains/feedback/handlers.py: handle_feedback_choice
        ├─> если отзыв → FSM: waiting_for_feedback_text
        ├─> если пропустить → завершение FSM
        └─> domains/feedback/handlers.py: handle_feedback_text
              ├─> domains/feedback/service.py: save_feedback_text
              │     └─> PostgreSQL: обновление GenerationFeedback текстом отзыва
              └─> завершение FSM
```

Текстовый отзыв необязателен: пользователь может завершить сценарий после выставления оценки.

## Событийная архитектура
Приложение использует событийную архитектуру на базе TaskIQ и RabbitMQ:

**Команды** (shared/contracts/commands.py):
- `CheckCreditsCommand` — проверить кредиты пользователя
- `EnrichPromptCommand` — обогатить промпт через LLM
- `StartGenerationCommand` — начать генерацию музыки

**События** (shared/contracts/events.py):
- `PromptEnriched` — промпт успешно обогащён
- `GenerationSucceeded` — генерация завершена успешно
- `GenerationFailed` — генерация завершена с ошибкой
- `EvaluationCompleted` — пользователь оценил генерацию

**Воркеры**:
- `domains/credits/worker.py` — обработка проверки кредитов
- `domains/enricher/worker.py` — обработка обогащения промптов
- `domains/generation/worker.py` — обработка генерации музыки
- `domains/evaluation/worker.py` — обработка запроса оценки

Приложение использует событийную архитектуру на базе TaskIQ и RabbitMQ:

**Команды** (shared/contracts/commands.py):
- `CheckCreditsCommand` — проверить кредиты пользователя
- `EnrichPromptCommand` — обогатить промпт через LLM
- `StartGenerationCommand` — начать генерацию музыки

**События** (shared/contracts/events.py):
- `PromptEnriched` — промпт успешно обогащён
- `GenerationSucceeded` — генерация завершена успешно
- `GenerationFailed` — генерация завершена с ошибкой
- `EvaluationCompleted` — пользователь оценил генерацию

**Воркеры**:
- `domains/credits/worker.py` — обработка проверки кредитов
- `domains/enricher/worker.py` — обработка обогащения промптов
- `domains/generation/worker.py` — обработка генерации музыки
- `domains/evaluation/worker.py` — обработка запроса оценки

### 5. Обработка ошибок

```text
Ошибка в обработчике или воркере
  └─> domains/.../handlers.py: локальная обработка, если предусмотрена
        └─> core/utils/error_notify.py: notify_owner

Необработанная ошибка
  └─> bot.py: on_error
        └─> core/utils/error_notify.py: notify_owner
```

`notify_owner` записывает ошибку в лог и отправляет владельцу traceback с контекстом, если настроен `BOT_OWNER_ID`.

### 6. Порты и адаптеры

Для изоляции воркеров от aiogram используется паттерн портов и адаптеров (shared/ports/):

- `TelegramPort` — абстрактный интерфейс для отправки сообщений, аудио и уведомлений
- `AiogramTelegramPort` — реализация на основе aiogram Bot

| Данные | Где хранятся | Назначение |
| --- | --- | --- |
| FSM-состояния и данные диалогов | Redis через RedisStorage Aiogram | Сохранение текущего этапа сценария между обновлениями и рестартами |
| Пользователи и авторизация | PostgreSQL, таблица `users` | Статус доступа пользователя |
| Ожидающие авторизацию и счётчики попыток | Redis, ключи `auth:pending:*` | Авторизация и защита от перебора ключа |
| Глобальный реестр активных генераций | Redis, множество `generations:active` | Проверка одновременных генераций пользователя |
| Флаги отмены генерации | Redis, ключи `generation:cancel:*` | Сигнал воркеру о необходимости остановки |
| Активные задачи генерации | Память процесса; набор user_id — Redis | Возможность отменить выполняющуюся задачу |
| Промпты и названия генераций | PostgreSQL, таблица `generations` | История генераций |
| Оценки и отзывы | PostgreSQL, таблица `generation_feedbacks` | Обратная связь о генерациях |
| Очереди и сообщения TaskIQ | RabbitMQ (продакшн) или InMemoryBroker (тесты) | Асинхронная обработка команд и событий |
| Тестовые данные | In-memory SQLite, fakeredis и InMemoryBroker | Изолированные тесты без боевых подключений |

## Тестирование

TaskIQ-воркеры могут работать отдельно от бота и масштабироваться независимо.
│   │   ├── task_registry.py           # Глобальный реестр активных генераций в Redis
│       │   ├── handlers.py            # /logout, ввод ключа, фильтры авторизации и fallback
│       │   ├── service.py             # Проверка авторизации, ключ доступа и операции с Redis/БД
│       │   ├── base_messages.py       # Текстовые сообщения базового домена
│       │   ├── credits_messages.py    # Текстовые сообщения домена кредитов
│       │   ├── enricher_messages.py   # Текстовые сообщения домена обогащения
│       │   ├── state_models.py        # Pydantic-модели для FSM-данных обогащения
│       │   ├── validator.py           # Разбор и нормализация ответа обогатителя
│       │   │   ├── integration/
│       │   │   │   ├── test_enricher.py           # Интеграционные тесты сервиса обогащения
│       │   │   │   ├── test_enricher_handlers.py  # Интеграционные тесты хендлеров
│       │   │   │   └── test_enricher_worker.py    # Интеграционные тесты воркера
│       │   │   └── unit/
│       │   │       ├── test_enricher_handlers.py  # Юнит-тесты хендлеров обогащения
│       │   │       ├── test_enricher_validator.py # Юнит-тесты валидатора
│       │   │       └── test_services_enricher.py  # Юнит-тесты сервиса обогащения
│       │   └── worker.py              # TaskIQ-воркер для обогащения промптов
│       ├── evaluation/
│       │   ├── evaluation_messages.py # Текстовые сообщения домена оценки
│       │   │   ├── integration/
│       │   │   └── unit/
│       │   └── worker.py              # TaskIQ-воркер для запроса оценки после генерации
│       ├── feedback/
│       │   ├── feedback_messages.py   # Текстовые сообщения домена отзывов
│       │   │   ├── integration/
│       │   │   └── unit/
│       │   │       ├── test_feedback_handlers.py  # Юнит-тесты хендлеров отзывов
│       │   │       └── test_services_feedback.py  # Юнит-тесты сервиса отзывов
│       │   └── state_models.py        # Pydantic-модели для FSM-данных отзывов
│       │   ├── generation_messages.py # Текстовые сообщения домена генерации
│       │   ├── state_models.py        # Pydantic-модели для FSM-данных генерации
│       │   │   ├── integration/
│       │   │   │   ├── taskiq_test_runner.py      # Вспомогательные утилиты для тестов воркера
│       │   │   │   ├── test_generation_flow.py    # Интеграционные тесты флоу генерации
│       │   │   │   └── test_generation_worker.py  # Интеграционные тесты воркера генерации
│       │   │   └── unit/
│       │   │       ├── test_progress_bar.py       # Юнит-тесты прогресс-бара
│       │   │       ├── test_redis_lock.py         # Юнит-тесты Redis-локов
│       │   │       ├── test_services_generation.py # Юнит-тесты сервиса генерации
│       │   │       └── test_task_registry.py      # Юнит-тесты реестра задач
│       │   └── worker.py              # TaskIQ-воркер для генерации музыки
├── shared/
│   ├── contracts/
│   │   ├── commands.py                # Команды для TaskIQ (CheckCreditsCommand, и т.д.)
│   │   └── events.py                  # События для TaskIQ (GenerationSucceeded, и т.д.)
│   └── ports/
│       ├── telegram.py                # Абстрактный порт TelegramPort и реализация AiogramTelegramPort
│       └── fake_telegram.py           # Фейковая реализация порта для тестов
├── workers/
│   └── taskiq_worker.py               # Главный воркер TaskIQ с регистрацией всех доменных воркеров
├── conftest.py                        # Общие фикстуры для тестов (make_message, fake_state)
├── mock_generation.json               # Mock-данные для локальной генерации
└── __init__.py
tests/                                 # Smoke-тесты инфраструктуры (вне src)
    └── smoke/
        ├── test_broker_connection.py  # Проверка подключения к RabbitMQ
        ├── test_db_connection.py      # Проверка подключения к PostgreSQL
        └── test_redis_connection.py   # Проверка подключения к Redis

- `FakeTelegramPort` — фейковая реализация для тестов, сохраняющая историю вызовов

### 6. Порты и адаптеры

Для изоляции воркеров от aiogram используется паттерн портов и адаптеров (shared/ports/):

- `TelegramPort` — абстрактный интерфейс для отправки сообщений, аудио и уведомлений
- `AiogramTelegramPort` — реализация на основе aiogram Bot
Проект содержит несколько уровней тестов:

**Юнит-тесты** (`src/domains/*/tests/unit/`):
- Тестируют отдельные функции и методы в изоляции
- Используют моки для внешних зависимостей
- Покрывают прогресс-бар, валидаторы, сервисную логику

**Интеграционные тесты** (`src/domains/*/tests/integration/`):
- Тестируют взаимодействие компонентов
- Используют реальные SQLite, fakeredis и InMemoryBroker
- Покрывают полные флоу хендлеров и воркеров
**Smoke-тесты** (`tests/smoke/`):
- Проверяют доступность инфраструктуры (RabbitMQ, PostgreSQL, Redis)
- Запускаются перед деплоем для валидации окружения
## Деплой и жизненный цикл

Приложение поддерживает два режима работы с Telegram:
- **Polling** — для разработки и тестирования
- **Webhook** — для продакшна, управляется флагом `WEBHOOK_ENABLED`

Graceful shutdown (core/lifecycle.py):
- Корректное завершение обработки текущих апдейтов
- Закрытие соединений с БД, Redis и брокером
- Очистка реестров и временных ресурсов
│   │   ├── database/
│       │   └── ports.py               # Реэкспорт портов Telegram
│       ├── auth/
│       │   ├── auth_messages.py       # Текстовые сообщения домена авторизации
│       ├── base/
│       │   └── worker.py              # TaskIQ-воркер для проверки кредитов
│       ├── credits/
│       │   ├── enricher_messages.py   # Текстовые сообщения домена обогащения
│       │   ├── state_models.py        # Pydantic-модели для FSM-данных обогащения
│       │   ├── validator.py           # Разбор и нормализация ответа обогатителя
│       │   │   ├── integration/
│       │   │   │   ├── test_enricher.py           # Интеграционные тесты сервиса обогащения
│       │   │   │   ├── test_enricher_handlers.py  # Интеграционные тесты хендлеров
│       │   │   │   └── test_enricher_worker.py    # Интеграционные тесты воркера
│       │   │   └── unit/
│       │   │       ├── test_enricher_handlers.py  # Юнит-тесты хендлеров обогащения
│       │   │       ├── test_enricher_validator.py # Юнит-тесты валидатора
│       │   │       └── test_services_enricher.py  # Юнит-тесты сервиса обогащения
│       │   └── worker.py              # TaskIQ-воркер для обогащения промптов
│       ├── evaluation/
│       │   ├── evaluation_messages.py # Текстовые сообщения домена оценки
│       │   │   ├── integration/
│       │   │   └── unit/
│       │   └── worker.py              # TaskIQ-воркер для запроса оценки после генерации
│       ├── feedback/
│       │   ├── feedback_messages.py   # Текстовые сообщения домена отзывов
│       │   │   ├── integration/
│       │   │   └── unit/
│       │   │       ├── test_feedback_handlers.py  # Юнит-тесты хендлеров отзывов
│       │   │       └── test_services_feedback.py  # Юнит-тесты сервиса отзывов
│       │   └── state_models.py        # Pydantic-модели для FSM-данных отзывов
│       └── generation/
│           ├── generation_messages.py # Текстовые сообщения домена генерации
│           ├── state_models.py        # Pydantic-модели для FSM-данных генерации
│           ├── pipeline_handlers.py   # Telegram-часть конвейера, FSM, прогресс и отправка аудио
│           └── tests/
│               ├── integration/
│               │   ├── taskiq_test_runner.py      # Вспомогательные утилиты для тестов воркера
│               │   ├── test_generation_flow.py    # Интеграционные тесты флоу генерации
│               │   └── test_generation_worker.py  # Интеграционные тесты воркера генерации
│               └── unit/
│                   ├── test_progress_bar.py       # Юнит-тесты прогресс-бара
│                   ├── test_redis_lock.py         # Юнит-тесты Redis-локов
│                   ├── test_services_generation.py # Юнит-тесты сервиса генерации
│                   └── test_task_registry.py      # Юнит-тесты реестра задач
│           └── worker.py              # TaskIQ-воркер для генерации музыки
├── shared/
│   ├── contracts/
│   │   ├── commands.py                # Команды для TaskIQ (CheckCreditsCommand, и т.д.)
│   │   └── events.py                  # События для TaskIQ (GenerationSucceeded, и т.д.)
│   └── ports/
│       ├── telegram.py                # Абстрактный порт TelegramPort и реализация AiogramTelegramPort
│       └── fake_telegram.py           # Фейковая реализация порта для тестов
├── workers/
│   └── taskiq_worker.py               # Главный воркер TaskIQ с регистрацией всех доменных воркеров
├── conftest.py                        # Общие фикстуры для тестов (make_message, fake_state)
├── mock_generation.json               # Mock-данные для локальной генерации
└── __init__.py
tests/                                 # Smoke-тесты инфраструктуры (вне src)
    └── smoke/
        ├── test_broker_connection.py  # Проверка подключения к RabbitMQ
        ├── test_db_connection.py      # Проверка подключения к PostgreSQL
        └── test_redis_connection.py   # Проверка подключения к Redis

- `FakeTelegramPort` — фейковая реализация для тестов, сохраняющая историю вызовов

## Хранение состояния и данных

TaskIQ-воркеры могут работать отдельно от бота и масштабироваться независимо.
