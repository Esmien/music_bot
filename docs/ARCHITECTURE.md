# Архитектура проекта

## System Design

Событийно-ориентированное приложение с распределением кода по доменам и асинхронной обработкой задач.

- **Aiogram-бот** принимает сообщения и callback-запросы Telegram
- **Обработчики доменов** управляют Telegram-взаимодействием, FSM-сценариями и публикуют команды/события в брокер
- **Воркеры доменов** слушают команды и события из брокера и выполняют фоновые задачи
- **Сервисы доменов** содержат бизнес-логику и интеграции с внешними API и БД
- **RabbitMQ** используется как брокер сообщений для TaskIQ (продакшн)
- **InMemoryBroker** используется для тестов и локальной разработки
- **TaskIQ** обеспечивает асинхронную обработку задач и событийную связь между компонентами
- **PostgreSQL** хранит пользователей и данные о генерациях, оценках и отзывах
- **Redis** используется для FSM-состояний и служебных реестров
- **SQLite, fakeredis и InMemoryBroker** применяются в тестах

## Структура проекта

```text
.
├── src/
│   ├── bot.py                         # Инициализация бота, Dispatcher, Redis FSM и webhook/polling
│   ├── core/
│   │   ├── __init__.py                # Сборка доменных Router в единый Router
│   │   ├── broker.py                  # Инициализация TaskIQ брокера (RabbitMQ или InMemory)
│   │   ├── lifecycle.py               # Управление жизненным циклом приложения и graceful shutdown
│   │   ├── config.py                  # Настройки окружения (BotConfig, DatabaseConfig, RabbitMQConfig и т.д.)
│   │   ├── redis.py                   # Единый async-клиент Redis и функции для ключей реестров
│   │   ├── database/
│   │   │   ├── __init__.py            # Реэкспорт engine, get_session и моделей
│   │   │   ├── engine.py              # Async SQLAlchemy engine, get_session, init_db()
│   │   │   └── models.py              # Базовый класс Base для ORM-моделей
│   │   └── utils/
│   │       ├── error_notify.py        # Логирование ошибок и уведомление владельца
│   │       ├── exceptions.py          # Кастомные исключения (APINotSet, GenerationConfigurationError и т.д.)
│   │       └── fsm_helpers.py         # Вспомогательные функции для работы с FSM
│   ├── domains/
│   │   ├── auth/
│   │   │   ├── handlers.py            # /logout, ввод ключа, фильтры авторизации и fallback
│   │   │   ├── service.py             # Проверка авторизации, ключ доступа, операции с Redis/БД
│   │   │   ├── auth_messages.py       # Текстовые сообщения домена авторизации
│   │   │   ├── registries/
│   │   │   │   └── auth_registry.py   # Операции с реестром ожидающих авторизацию в Redis
│   │   │   └── tests/
│   │   │       └── unit/
│   │   │           └── test_auth_flow.py  # Юнит-тесты авторизации
│   │   ├── base/
│   │   │   ├── handlers.py            # /start и /cancel
│   │   │   ├── keyboards.py           # Главное меню и клавиатура отмены
│   │   │   ├── models.py              # ORM-модель User
│   │   │   ├── service.py             # Получение названия последней генерации
│   │   │   ├── base_messages.py       # Текстовые сообщения базового домена
│   │   │   ├── worker.py              # TaskIQ-воркер для проверки кредитов
│   │   │   └── tests/
│   │   ├── credits/
│   │   │   ├── handlers.py            # Команда и кнопка проверки кредитов
│   │   │   ├── service.py             # OpenRouter API и пересчёт баланса в генерации
│   │   │   ├── credits_messages.py    # Текстовые сообщения домена кредитов
│   │   │   └── tests/
│   │   │       └── integration/
│   │   │           └── test_credits.py        # Интеграционные тесты сервиса кредитов
│   │   ├── enricher/
│   │   │   ├── handlers.py            # FSM-диалог идеи, обогащения, правок и подтверждения
│   │   │   ├── fsm.py                 # Состояния сценария обогащения
│   │   │   ├── keyboards.py           # Клавиатуры обогащения и выбора названия
│   │   │   ├── service.py             # API обогатителя, валидация и сохранение результата
│   │   │   ├── validator.py           # Разбор и нормализация ответа обогатителя
│   │   │   ├── worker.py              # TaskIQ-воркер для обогащения промптов
│   │   │   ├── enricher_messages.py   # Текстовые сообщения домена обогащения
│   │   │   ├── state_models.py        # Pydantic-модели для FSM-данных обогащения
│   │   │   └── tests/
│   │   │       ├── integration/
│   │   │       │   ├── test_enricher.py           # Интеграционные тесты сервиса
│   │   │       │   ├── test_enricher_handlers.py  # Интеграционные тесты хендлеров
│   │   │       │   └── test_enricher_worker.py    # Интеграционные тесты воркера
│   │   │       └── unit/
│   │   │           ├── test_enricher_handlers.py  # Юнит-тесты хендлеров
│   │   │           ├── test_enricher_validator.py # Юнит-тесты валидатора
│   │   │           └── test_services_enricher.py  # Юнит-тесты сервиса
│   │   ├── evaluation/
│   │   │   ├── handlers.py            # Приём оценки, публикация события EvaluationCompleted
│   │   │   ├── fsm.py                 # Реэкспорт FeedbackStates для совместимости
│   │   │   ├── keyboards.py           # Клавиатура оценки (лайк/дизлайк)
│   │   │   ├── service.py             # Сохранение оценки в БД
│   │   │   ├── evaluation_messages.py # Текстовые сообщения домена оценки
│   │   │   └── tests/
│   │   │       ├── integration/
│   │   │       └── unit/
│   │   ├── feedback/
│   │   │   ├── handlers.py            # Выбор действия после оценки, приём отзыва и завершение
│   │   │   ├── fsm.py                 # Состояния оценки и сбора отзыва
│   │   │   ├── keyboards.py           # Клавиатуры выбора действия и отправки отзыва
│   │   │   ├── models.py              # ORM-модель GenerationFeedback (оценки и отзывы)
│   │   │   ├── service.py             # Сохранение текстовых отзывов в БД
│   │   │   ├── feedback_messages.py   # Текстовые сообщения домена отзывов
│   │   │   ├── state_models.py        # Pydantic-модели для FSM-данных отзывов
│   │   │   └── tests/
│   │   │       ├── integration/
│   │   │       └── unit/
│   │   │           ├── test_feedback_handlers.py  # Юнит-тесты хендлеров
│   │   │           └── test_services_feedback.py  # Юнит-тесты сервиса
│   │   └── generation/
│   │       ├── handlers.py            # Запуск генерации, повтор и обработка названия
│   │       ├── pipeline_handlers.py   # Telegram-часть конвейера, FSM, прогресс и отправка аудио
│   │       ├── fsm.py                 # Состояния, ограничения длины и очистка FSM-флагов
│   │       ├── keyboards.py           # Клавиатура повтора генерации
│   │       ├── models.py              # ORM-модель Generation и enum GenerationStatus
│   │       ├── service.py             # Генерация через OpenRouter или MOCK_MODE, локи и прогресс
│   │       ├── worker.py              # TaskIQ-воркер для генерации музыки
│   │       ├── generation_messages.py # Текстовые сообщения домена генерации
│   │       ├── state_models.py        # Pydantic-модели для FSM-данных генерации
│   │       ├── registries/
│   │       │   └── task_registry.py   # Доменный реестр активных задач генерации (legacy)
│   │       └── tests/
│   │           ├── integration/
│   │           │   ├── taskiq_test_runner.py      # Вспомогательные утилиты для тестов воркера
│   │           │   ├── test_generation_flow.py    # Интеграционные тесты флоу генерации
│   │           │   └── test_generation_worker.py  # Интеграционные тесты воркера
│   │           └── unit/
│   │               ├── test_progress_bar.py       # Юнит-тесты прогресс-бара
│   │               ├── test_redis_lock.py         # Юнит-тесты Redis-локов
│   │               ├── test_services_generation.py # Юнит-тесты сервиса генерации
│   │               └── test_task_registry.py      # Юнит-тесты реестра задач
│   ├── shared/
│   │   ├── contracts/
│   │   │   ├── commands.py            # Команды для TaskIQ (EnrichPromptCommand, StartGenerationCommand и т.д.)
│   │   │   └── events.py              # События для TaskIQ (EnrichmentCompleted, GenerationSucceeded и т.д.)
│   │   └── ports/
│   │       ├── telegram.py            # Абстрактный порт TelegramPort и реализация AiogramTelegramPort
│   │       └── fake_telegram.py       # Фейковая реализация порта для тестов
│   ├── conftest.py                    # Общие фикстуры для тестов (make_message, fake_state)
│   └── mock_generation.json           # Mock-данные для локальной генерации
├── workers/
│   └── taskiq_worker.py               # Главный воркер TaskIQ с регистрацией всех доменных воркеров
├── tests/                             # Smoke-тесты инфраструктуры (вне src)
│   └── smoke/
│       ├── test_broker_connection.py  # Проверка подключения к RabbitMQ
│       ├── test_db_connection.py      # Проверка подключения к PostgreSQL
│       └── test_redis_connection.py   # Проверка подключения к Redis
├── infra/                             # Dockerfile, docker-compose и entrypoint
├── migrations/                        # Миграции Alembic
├── docs/                              # Документация проекта и задачи
├── pyproject.toml                     # Poetry, Ruff, Pytest и coverage
└── README.md
```

В каждом домене размещены его обработчики, воркеры, сервисы, состояния, клавиатуры, сообщения и тесты — если соответствующие компоненты нужны домену. Общая инфраструктура и базовые ORM-модели находятся в `core`. Контракты команд и событий, а также порты для абстракции Telegram-взаимодействия находятся в `shared`.

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

`/credits` или кнопка проверки кредитов обрабатывается Telegram-хендлером синхронно:

```text
Пользователь
  └─> domains/credits/handlers.py: cmd_credits
        ├─> domains/credits/service.py: get_credits_summary
        │     └─> OpenRouter API: баланс ключа
        ├─> перевод долларов в примерное число генераций
        └─> отправка результата пользователю в чат
```

### 2. Обогащение промпта (событийный флоу)

```text
Пользователь вводит идею
  └─> domains/enricher/handlers.py: handle_idea
        ├─> сохранение идеи в FSM
        ├─> публикация EnrichPromptCommand в TaskIQ
        └─> FSM: PromptEnricherStates.waiting_enrichment

Воркер получает команду EnrichPromptCommand
  └─> domains/enricher/worker.py: enrich_prompt_handler
        ├─> domains/enricher/service.py: enrich_prompt
        │     └─> внешний OpenAI-совместимый API
        ├─> domains/enricher/validator.py: parse_enricher_json
        ├─> отправка обогащённого промпта через TelegramPort
        └─> публикация события EnrichmentCompleted

Хендлер события EnrichmentCompleted
  └─> domains/enricher/handlers.py: handle_enrichment_completed_event
        ├─> обновление FSM-состояния пользователя
        └─> FSM: PromptEnricherStates.waiting_confirmation

Пользователь подтверждает промпт
  └─> domains/enricher/handlers.py: handle_enriched_prompt_confirmation
        ├─> сохранение промпта в БД (создание Generation)
        ├─> FSM: GenerationStates.waiting_title
        └─> запрос названия трека

Пользователь вводит название
  └─> domains/generation/handlers.py: handle_title_input
        ├─> обновление названия в БД
        ├─> публикация StartGenerationCommand в TaskIQ
        └─> FSM: GenerationStates.generating
```

### 3. Генерация музыки (событийный флоу)

```text
Воркер получает команду StartGenerationCommand
  └─> domains/generation/worker.py: start_generation_handler
        ├─> получение данных генерации из БД
        ├─> проверка Redis-локов (один пользователь = одна генерация)
        ├─> domains/generation/service.py: run_generation
        │     ├─> MOCK_MODE: локальный mock-файл (mock_generation.json)
        │     └─> OpenRouter: SSE-поток и сборка MP3 из base64-чанков
        │           ├─> retry-политика с экспоненциальным backoff
        │           ├─> проверка флага отмены через Redis
        │           └─> отправка прогресса через TelegramPort
        ├─> отправка аудио через TelegramPort
        ├─> PostgreSQL: обновление статуса генерации на SUCCESS
        └─> публикация события GenerationSucceeded

При успешной генерации
  └─> domains/generation/worker.py: run_generation_task
        ├─> публикация события GenerationSucceeded
        └─> domains/evaluation/handlers.py: handle_generation_succeeded_event
              ├─> проверка соответствия gen_id в FSM
              ├─> FSM: FeedbackStates.waiting_evaluation
              └─> отправка клавиатуры оценки пользователю через TelegramPort

При ошибке генерации
  └─> domains/generation/worker.py: run_generation_task
        ├─> уведомление владельца через notify_owner
        ├─> PostgreSQL: обновление статуса на FAILED
        ├─> публикация события GenerationFailed(stage="generation")
        └─> domains/generation/handlers.py: handle_generation_failed_event
              ├─> сброс FSM
              └─> отправка сообщения об ошибке
```

`/cancel` и `/logout` могут отменить задачу через Redis-флаг `generation:cancel:{gen_id}`, который проверяется воркером генерации во время выполнения.

### 4. Оценка и отзыв (Telegram-хендлеры)

```text
Пользователь ставит оценку (лайк/дизлайк)
  └─> domains/evaluation/handlers.py: handle_evaluate
        ├─> извлечение gen_id из callback_data и проверка соответствия FSM state
        ├─> domains/feedback/service.py: save_feedback (с gen_id и user_id)
        │     └─> PostgreSQL: upsert в GenerationFeedback с проверкой принадлежности
        ├─> обновление FSM state с gen_id и оценкой
        └─> FSM: FeedbackStates.waiting_for_feedback_choice

Пользователь выбирает действие (оставить отзыв или завершить)
  └─> domains/feedback/handlers.py: handle_feedback_send_choice
        ├─> если отзыв → FSM: FeedbackStates.waiting_feedback
        └─> если завершить → handle_feedback_finish_choice
              ├─> очистка FSM
              └─> отправка благодарности

Пользователь вводит текстовый отзыв
  └─> domains/feedback/handlers.py: handle_feedback_message
        ├─> domains/feedback/service.py: save_feedback
        │     └─> PostgreSQL: сохранение текста отзыва в GenerationFeedback
        ├─> очистка FSM
        └─> отправка благодарности
```

Текстовый отзыв необязателен: пользователь может завершить сценарий сразу после выставления оценки.

## Событийная архитектура

Приложение использует событийную архитектуру на базе TaskIQ и RabbitMQ для длительных фоновых задач.

**Команды** (src/shared/contracts/commands.py):
- `EnrichPromptCommand` — обогатить промпт через LLM
- `StartGenerationCommand` — начать генерацию музыки
- `CheckCreditsCommand` — проверить баланс кредитов через OpenRouter API

**События** (src/shared/contracts/events.py):
- `EnrichmentCompleted` — промпт успешно обогащён
- `GenerationSucceeded` — генерация завершена успешно
- `GenerationFailed` — генерация завершена с ошибкой

**Воркеры**:
- `domains/enricher/worker.py` — обработка обогащения промптов
- `domains/generation/worker.py` — обработка генерации музыки
- `domains/base/worker.py` — проверка кредитов по команде

Все воркеры регистрируются в главном процессе воркера: `workers/taskiq_worker.py`

### 5. Обработка ошибок

```text
Ошибка в Telegram-хендлере
  └─> локальная обработка в try/except, если предусмотрена
        └─> core/utils/error_notify.py: notify_owner

Необработанная ошибка в хендлере
  └─> bot.py: on_error (глобальный error handler)
        └─> core/utils/error_notify.py: notify_owner

Ошибка в TaskIQ-воркере
  └─> локальная обработка в воркере
        ├─> логирование с контекстом (gen_id, user_id)
        ├─> core/utils/error_notify.py: notify_owner
        └─> публикация события о сбое (например, GenerationFailed)
```

`notify_owner` записывает ошибку в лог и отправляет владельцу traceback с контекстом через Telegram, если настроен `BOT_OWNER_ID`.

### 6. Порты и адаптеры

Для изоляции воркеров от aiogram используется паттерн портов и адаптеров (src/shared/ports/):

- `TelegramPort` — абстрактный интерфейс для отправки сообщений, аудио и уведомлений
- `AiogramTelegramPort` — реализация на основе aiogram Bot для продакшна
- `FakeTelegramPort` — фейковая реализация для тестов, сохраняющая историю вызовов

Это позволяет воркерам работать без прямой зависимости от aiogram и упрощает тестирование.

## Хранение состояния и данных

| Данные | Где хранятся | Назначение |
| --- | --- | --- |
| FSM-состояния и данные диалогов | Redis через RedisStorage aiogram | Сохранение текущего этапа сценария между обновлениями и рестартами |
| Пользователи и авторизация | PostgreSQL, таблица `users` | Статус доступа пользователя (is_authorized, created_at) |
| Ожидающие авторизацию и счётчики попыток | Redis, ключи `auth:pending:{user_id}` | Авторизация и защита от перебора ключа (лимит попыток) |
| Глобальный реестр активных генераций | Redis, множество `generations:active` | Проверка одновременных генераций пользователя |
| Флаги отмены генерации | Redis, ключи `generation:cancel:{gen_id}` | Сигнал воркеру о необходимости остановки задачи |
| Промпты и названия генераций | PostgreSQL, таблица `generations` | История генераций (user_id, prompt, title, status, created_at) |
| Аудио-артефакты генераций | Локальное файловое хранилище (AUDIO_STORAGE_PATH) | Сохраненные mp3-файлы с метаданными (путь, размер, checksum) в БД |
| Оценки и отзывы | PostgreSQL, таблица `generation_feedbacks` | Обратная связь о генерациях (generation_id, is_positive, comment) |
| Очереди и сообщения TaskIQ | RabbitMQ (продакшн) или InMemoryBroker (тесты) | Асинхронная обработка команд и событий |
| Тестовые данные | In-memory SQLite, fakeredis и InMemoryBroker | Изолированные тесты без боевых подключений |

## Тестирование

Проект содержит несколько уровней тестов:

**Юнит-тесты** (`src/domains/*/tests/unit/`):
- Тестируют отдельные функции и методы в изоляции
- Используют моки для внешних зависимостей (API, БД, Redis)
- Покрывают валидаторы, прогресс-бар, сервисную логику, Redis-локи
- Быстрые, не требуют инфраструктуры

**Интеграционные тесты** (`src/domains/*/tests/integration/`):
- Тестируют взаимодействие компонентов (хендлеры + воркеры + БД)
- Используют реальные SQLite, fakeredis и InMemoryBroker
- Покрывают полные флоу: обогащение, генерация, оценка, отзывы
- Проверяют FSM-переходы и событийную связь

**Smoke-тесты** (`tests/smoke/`):
- Проверяют доступность инфраструктуры (RabbitMQ, PostgreSQL, Redis)
- Запускаются перед деплоем для валидации окружения
- Быстро выявляют проблемы конфигурации

**Общие фикстуры** (`src/conftest.py`):
- `make_message` — фабрика фейковых сообщений для хендлеров
- `fake_state` — фабрика фейковых FSM-контекстов с хранилищем в памяти

## Деплой и жизненный цикл

Приложение поддерживает два режима работы с Telegram:
- **Polling** — для разработки и тестирования (флаг `WEBHOOK_ENABLED=false`)
- **Webhook** — для продакшна (флаг `WEBHOOK_ENABLED=true`)

**Graceful shutdown** (core/lifecycle.py):
- Корректное завершение обработки текущих апдейтов
- Закрытие соединений с БД, Redis и брокером
- Очистка реестров и временных ресурсов
- Обработка сигналов SIGINT и SIGTERM

**TaskIQ-воркеры**:
- Могут работать отдельно от бота и масштабироваться независимо
- Запускаются через `taskiq worker workers.taskiq_worker:broker`
- Регистрируют все доменные воркеры в главном процессе
