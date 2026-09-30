# Аудит проекта `music-bot`

**Commit:** `f55bf4af599c` (dev)
**Дата:** 2026-09-28
**Scope:** один снимок репозитория
**Статус:** статический аудит; runtime-проверки не выполнены
**Ограничения:** нет `poetry.lock`; `pytest`, `ruff`, `mypy`, `alembic history` не запускались в изоляции

---

## 0. TL;DR

### Главные выводы

1. **Event handlers падают с `TypeError` в production.** `context["telegram_port"]` в `domains/enricher/handlers.py`, но `taskiq.Context` не subscriptable. Тесты маскируют баг через `TaskiqState` (dict).
2. **`AiogramTelegramPort.notify_owner()` несовместим с сигнатурой `core.notify_owner()`.** Передаёт `owner_id` и `message`, которых нет.
3. **Compose запускает 5 worker-контейнеров, launcher поддерживает 2.** `evaluation-worker`, `credits-worker`, `feedback-worker` — crash loop.
4. **`generation.worker` обошёл `GenerationSucceeded`** и сам управляет FSM оценки.
5. **Claim платной генерации не атомарен.** Два invocation → две оплаты.
6. **Retry обёрнут вокруг всей платной операции.** `ReadTimeout` после POST → второй POST.
7. **Аудио идёт в Telegram до фиксации `SUCCESS`.** Потеря консистентности.
8. **Ошибка генерации оставляет `PENDING`.** Нет terminal transition.
9. **Compose публикует Postgres/Redis/RabbitMQ порты наружу.** RabbitMQ с fallback `songai/songai`.
10. **Тесты маскируют production.** `TaskiqState` вместо `Context`, `InMemoryBroker` вместо RabbitMQ, SQLite вместо PostgreSQL.

### Оценка готовности

Снимок не готов к следующему production-деплою без закрытия P0/P1.

---

## 1. Findings Table

| ID | Severity | Category | Title | Priority |
|----|----------|----------|-------|----------|
| AUD-001 | HIGH | Инфраструктура | Compose запускает неподдерживаемые worker-домены | P1 |
| AUD-002 | BLOCKER | Конкурентность / деньги | Неатомарный claim платной генерации | P0 |
| AUD-003 | BLOCKER | Внешние API / деньги | Retry всей платной операции OpenRouter | P0 |
| AUD-004 | HIGH | Надёжность | Нет durable результата до Telegram | P1 |
| AUD-005 | HIGH | FSM | Ошибки оставляют generation в `PENDING` | P1 |
| AUD-006 | HIGH | Lifecycle | Polling не связан с shutdown event | P1 |
| AUD-007 | HIGH | Feedback | Оценка привязана к последней генерации | P1 |
| AUD-008 | HIGH | Безопасность | Инфраструктурные порты опубликованы наружу | P1 |
| AUD-009 | MEDIUM | Redis | TOCTOU в освобождении lock | P2 |
| AUD-010 | HIGH | SSE | Cumulative detection повреждает дельты | P1 |
| AUD-011 | HIGH | Telegram | `AiogramTelegramPort.notify_owner()` несовместим с сигнатурой | P1 |
| AUD-012 | MEDIUM | Тесты | Worker-тесты расходятся с реализацией | P2 |
| AUD-013 | MEDIUM | FSM | `/credits` очищает активный сценарий | P2 |
| AUD-014 | MEDIUM | Внешние API | Retry policy не единообразна | P2 |
| AUD-015 | MEDIUM | Очереди | DLQ заявлен, но не подтверждён | P2 |
| AUD-016 | LOW | Документация | Docs/DoD расходятся с кодом | P3 |
| AUD-017 | HIGH | Архитектура | Event flow расходится с заявленной моделью | P1 |
| AUD-018 | BLOCKER | TaskIQ / Telegram | `context["telegram_port"]` в event handlers | P0 |

---

## 2. Detailed Findings

### AUD-018 — `context["telegram_port"]` в event handlers

- **Severity:** BLOCKER
- **Evidence:** `domains/enricher/handlers.py` в `handle_enrichment_completed_event` и `handle_generation_failed_event` использует `context["telegram_port"]`, `context["storage"]`, `context["bot"]`. `taskiq.Context` не subscriptable. Тесты `test_event_handlers.py` передают `TaskiqState()` (dict) и маскируют production-баг.
- **Impact:** Доставка enriched prompt падает с `TypeError` до отправки. FSM не переходит в `waiting_for_approval`. Error path уведомлений тоже ломается.
- **Root cause:** Нет единого контракта на доступ к TaskIQ dependencies. Test fixture не моделирует runtime boundary.
- **Recommendation:** Все event handlers читают через `context.state["key"]` или единый helper. Тесты — через объект с `.state` или integration test.
- **DoD:** См. TASK-001.

### AUD-011 — `AiogramTelegramPort.notify_owner()` несовместим

- **Severity:** HIGH (confirmed)
- **Evidence:** `shared/ports/telegram.py:164–165` вызывает `core_notify_owner(bot=..., owner_id=..., message=..., context=...)`. Фактическая сигнатура `notify_owner()` принимает только `bot`, `context`, `err`, `telegram_port`.
- **Impact:** `TypeError` до отправки. Плюс рекурсия: `core.notify_owner(telegram_port=...)` → `TelegramPort.notify_owner(...)` → `core.notify_owner(bot=..., owner_id=...)`.
- **Recommendation:** Выбрать один уровень. Вариант A: `AiogramTelegramPort` напрямую `self._bot.send_message(...)`. Вариант B: `core.notify_owner` форматирует, порт отправляет.
- **DoD:** См. TASK-002.

### AUD-001 — Compose запускает неподдерживаемые worker-домены

- **Evidence:** `infra/docker-compose.yml:63–118` передаёт `evaluation`, `credits`, `feedback`; `workers/taskiq_worker.py:24–29,79` разрешает только `enricher` и `generation`.
- **Impact:** Crash loop, шум логов, ложная карта deployment.
- **DoD:** См. TASK-003.

### AUD-008 — Production Compose публикует инфраструктурные порты

- **Evidence:** Compose `133–134, 148–149, 165–167`; fallback credentials `161–162`.
- **Impact:** При доступности извне — атаки на БД, очереди, FSM. Redis без auth.
- **DoD:** См. TASK-004.

### AUD-006 — SIGTERM не останавливает polling

- **Evidence:** `bot.py:136–145` ставит `shutdown_event`; webhook ждёт, polling с `handle_signals=False` и event не использует `172–176`.
- **Impact:** Процесс не завершается штатно.
- **DoD:** См. TASK-005.

### AUD-013 — `/credits` очищает активный FSM

- **Evidence:** `credits/handlers.py:43` безусловный `state.clear()`.
- **Impact:** Теряются `gen_id`, `generating`; worker пропускает evaluation prompt.
- **DoD:** См. TASK-006.

### AUD-002 — Неатомарный claim платной генерации

- **Evidence:** `generation/worker.py:49–55` читает статус, закрывает сессию; `71` — POST. Атомарного перехода нет.
- **Impact:** Два invocation → две платные операции для одного `gen_id`.
- **DoD:** См. TASK-007.

### AUD-003 — Retry платного POST

- **Evidence:** `generation/service.py:71–72`, декоратор `291–297`.
- **Impact:** `ReadTimeout` после начала SSE → второй POST → повторное списание.
- **DoD:** См. TASK-008.

### AUD-017 — Расхождение event-driven модели

- **Evidence:** `generation/worker.py` сам переводит FSM в `waiting_evaluation`, отправляет `EVALUATION_PROMPT_TEXT` и keyboard, минуя `GenerationSucceeded`. Контракт в `shared/contracts/events.py:28–41` не используется. Diff в `instructions.md` показывает замену.
- **Impact:** Связанность с Telegram UI, дублирование orchestration, размытые границы доменов.
- **DoD:** См. TASK-009.

### AUD-004 — Нет durable аудио

- **Evidence:** `generation/worker.py:70–120`: `run_generation` → `send_audio` → commit `SUCCESS`. Артефакт только в памяти.
- **Impact:** Потеря при сбое, дубли при повторной доставке, неверный статус.
- **DoD:** См. TASK-010.

### AUD-005 — Error path не завершает lifecycle

- **Evidence:** `generation/worker.py:131–142` только логирует и уведомляет; сброс `generating=False` только в success path `110–112`.
- **Impact:** Остаётся `PENDING`, блокирует повторный запуск.
- **DoD:** См. TASK-011.

### AUD-007 — Feedback привязан к последней генерации

- **Evidence:** `feedback/service.py:16,39–46` принимает только `user_id`; `72–73` глотает `SQLAlchemyError`.
- **Impact:** Старые/чужие callbacks могут попасть не в тот трек; DB failure не различим.
- **DoD:** См. TASK-012.

### AUD-010 — Cumulative detection повреждает дельты

- **Evidence:** `generation/service.py:380–388` считает любой chunk с префиксом предыдущего cumulative snapshot.
- **Impact:** Две одинаковые дельты → потеря второй.
- **DoD:** См. TASK-013.

### AUD-009 — TOCTOU в Redis lock

- **Evidence:** `generation/service.py:132–136`: `GET` → сравнение → `DELETE`. Lua не используется.
- **Impact:** Старый owner может удалить новый lock.
- **DoD:** См. TASK-014.

### AUD-014 — Retry policy не единообразна

- **Evidence:** `credits/service.py:44–48`, `enricher/service.py:60–66`; `CONVENTIONS.md:85–92`.
- **Impact:** Временные сбои завершают сценарий без повтора.
- **DoD:** См. TASK-015.

### AUD-012 — Worker-тесты расходятся с реализацией

- **Evidence:** `test_generation_worker.py:68,111–123`; worker `44–47`.
- **Impact:** Регрессии не проверяются.
- **DoD:** См. TASK-016.

### AUD-015 — DLQ не подтверждён

- **Evidence:** `broker.py:26–29` задаёт `songai_dead_letters`; `config.py:122–131` формирует per-domain имена.
- **Impact:** Гипотеза: poison messages могут не сохраняться.
- **DoD:** См. TASK-016.

### AUD-016 — Docs/DoD расходятся с кодом

- **Evidence:** `ARCHITECTURE.md:126–139,195–209` описывает credits/evaluation worker. `backlog.md:276` утверждает замену всех `Any`; `core/types.py:11` содержит `Any`. `backlog.md:573,591` одновременно `TASK-001` в To Do и Done.
- **Impact:** Ложные гарантии, ошибки планирования.
- **DoD:** См. TASK-018.

---

## 3. Proposed Backlog

Порядок номеров = порядок реализации.

### P0 — BLOCKER

#### TASK-001 — Исправить доступ к TaskIQ dependencies в event handlers

- **Priority:** P0
- **Labels:** `bug`, `blocker`, `taskiq`, `reliability`, `testing`, `architecture`
- **Problem:** AUD-018. `context["telegram_port"]` в `domains/enricher/handlers.py` → `TypeError` в production. Тесты маскируют.
- **Solution:**
  1. Проверить фактический API TaskIQ.
  2. Заменить `context["key"]` → `context.state["key"]` во всех event handlers.
  3. Поиск по проекту: `context[`, `context.get(`, `TaskiqDepends`.
  4. Проверить injection в event task wrappers.
  5. Fixture — объект с `.state` или integration через TaskIQ.
  6. Static check на запрет `context[...]` в event handlers.
- **Tests:**
  - Unit: `telegram_port`, `storage`, `bot` через `context.state`.
  - Unit: missing dependency → диагностируемая ошибка.
  - Integration: `EnrichmentCompleted` через TaskIQ → `FakeTelegramPort.send_message`.
  - Integration: FSM в `waiting_for_approval`.
  - Static: `grep context[` — пусто в event handlers.
- **DoD:**
  - [ ] В event handlers нет `context[...]`.
  - [ ] Все читают через `context.state` или helper.
  - [ ] Проведён поиск всех `context[...]`.
  - [ ] Тесты больше не передают `TaskiqState` как production-контекст.
  - [ ] Есть integration test через TaskIQ execution path.
  - [ ] Ruff, Mypy, Pytest — без ошибок.
- **Estimate:** 0.5–1 день.

#### TASK-002 — Исправить `AiogramTelegramPort.notify_owner`

- **Priority:** P0
- **Labels:** `bug`, `blocker`, `telegram`, `reliability`
- **Problem:** AUD-011. `AiogramTelegramPort.notify_owner()` передаёт `owner_id`/`message` в `core.notify_owner()`. Рекурсивная связка.
- **Solution:**
  1. Выбрать вариант: A — порт отправляет напрямую через `self._bot.send_message(...)`; B — `core.notify_owner` форматирует, порт отправляет.
  2. Убрать неподдерживаемые kwargs.
  3. Устранить рекурсию.
  4. Уточнить контракт `TelegramPort.notify_owner()`.
- **Tests:**
  - Regression: `AiogramTelegramPort.notify_owner()` с monkeypatched Bot.
  - Проверка отсутствия `TypeError` при `BOT_OWNER_ID != 0`.
- **DoD:**
  - [ ] `AiogramTelegramPort.notify_owner()` совместим с `core.notify_owner()`.
  - [ ] Рекурсия устранена.
  - [ ] Regression test добавлен.
  - [ ] Ruff, Mypy, Pytest — без ошибок.
- **Estimate:** 0.5 дня.

#### TASK-003 — Удалить неиспользуемые worker units и broker connections

- **Priority:** P0
- **Labels:** `bug`, `reliability`, `tech-debt`, `docs`
- **Problem:** AUD-001. Compose запускает `evaluation-worker`, `credits-worker`, `feedback-worker`, launcher их отклоняет.
- **Solution:**
  1. Целевые process boundaries: `bot`, `enricher-worker`, `generation-worker`.
  2. Найти использования `evaluation_broker`, `credits_broker`, `feedback_broker`, `.kiq()`, `kicker(...)`.
  3. Удалить три сервиса из `infra/docker-compose.yml`.
  4. Удалить неиспользуемые broker instances.
  5. Не удалять домены и их Telegram handlers/services.
  6. Обновить `ARCHITECTURE.md`, `README.md`, `.env.example`, `docs/backlog.md`.
  7. Автопроверка соответствия worker commands launcher'у.
- **Tests:** CLI smoke для `enricher`, `generation`; static test worker names; `docker compose config`; импорт оставшихся worker modules.
- **DoD:**
  - [ ] В Compose только `enricher-worker`, `generation-worker`.
  - [ ] Нет неиспользуемых broker connections.
  - [ ] `domains/evaluation`, `domains/credits`, `domains/feedback` импортируются.
  - [ ] Telegram tests для credits/evaluation/feedback проходят.
  - [ ] `docker compose config` — код 0.
  - [ ] Docs обновлены.
  - [ ] Ruff, Mypy, Pytest — без ошибок.
- **Estimate:** 0.5–1 день.

#### TASK-004 — Закрыть production infrastructure ports

- **Priority:** P0
- **Labels:** `security`, `reliability`
- **Problem:** AUD-008. Опубликованы `5432`, `6379`, `5672`, `15672`. RabbitMQ с fallback `songai/songai`.
- **Solution:**
  1. Production Compose без ports.
  2. Local override с привязкой к `127.0.0.1`.
  3. Обязательные credentials RabbitMQ, убрать fallback.
  4. Обновить `.env.example`.
- **Tests:** Static check resolved Compose — нет host ports у infra services.
- **DoD:**
  - [ ] Production Compose не публикует `5432`, `6379`, `5672`, `15672`.
  - [ ] Local ports привязаны к `127.0.0.1`.
  - [ ] Пустые RabbitMQ credentials → отказ конфигурации.
- **Estimate:** 0.5 дня.

#### TASK-005 — Исправить shutdown бота

- **Priority:** P0
- **Labels:** `bug`, `reliability`
- **Problem:** AUD-006. Polling не связан с `shutdown_event`.
- **Solution:**
  1. Единый signal coordinator.
  2. Исправить polling branch: либо `dp.stop_polling()`, либо штатные сигналы aiogram.
  3. Process test SIGTERM.
  4. `stop_grace_period` по измеренному бюджету.
- **Tests:** Process test — SIGTERM без SIGKILL; cleanup при частичном startup.
- **DoD:**
  - [ ] Polling завершается по SIGTERM.
  - [ ] Cleanup выполняется при частичном startup.
  - [ ] `stop_grace_period` задан.
- **Estimate:** 0.5–1 день.

#### TASK-006 — Убрать `state.clear()` из `/credits`

- **Priority:** P0
- **Labels:** `bug`, `fsm`
- **Problem:** AUD-013. `/credits` сбрасывает активный сценарий.
- **Solution:** Удалить безусловный `state.clear()`.
- **Tests:** `/credits` во время enrichment, generation, feedback — FSM неизменен.
- **DoD:**
  - [ ] До и после `/credits` FSM state/data совпадают.
  - [ ] Активный worker сохраняет `gen_id`.
  - [ ] Regression tests добавлены.
- **Estimate:** 0.5 дня.

#### TASK-007 — Атомарный claim платной генерации

- **Priority:** P0
- **Labels:** `bug`, `money-loss`, `reliability`
- **Problem:** AUD-002.
- **Solution:**
  1. `PROCESSING`, `attempt_id`, timestamps.
  2. Conditional `UPDATE ... WHERE status='pending' RETURNING id`.
  3. Проверка владельца попытки при завершении.
  4. Recovery для неопределённых исходов.
- **Tests:** Два независимых PostgreSQL connections, barrier; mock API — ровно один вызов.
- **DoD:**
  - [ ] Миграция и downgrade проверены.
  - [ ] Конкурентный PostgreSQL-тест — ровно один API call.
  - [ ] Повторная доставка не вызывает API.
  - [ ] Docs обновлены.
- **Estimate:** 2–3 дня.

#### TASK-008 — Безопасная политика повторов OpenRouter

- **Priority:** P0
- **Labels:** `bug`, `money-loss`, `reliability`
- **Problem:** AUD-003.
- **Solution:**
  1. Разделить pre-request и post-request failures.
  2. `UNKNOWN` для неопределённых исходов.
  3. Типизированные HTTP errors.
  4. Документировать policy.
- **Tests:** Partial SSE → timeout не вызывает второй POST; `400` со словом `503` не повторяется.
- **DoD:**
  - [ ] Partial SSE → timeout не даёт второй POST.
  - [ ] `400` с `503` в тексте не повторяется.
  - [ ] Warning logs с `gen_id`, `attempt_id`, фазой.
  - [ ] Policy в `CONVENTIONS.md`.
- **Estimate:** 1–2 дня.
- **Dependencies:** TASK-007.

### P1 — HIGH

#### TASK-009 — Привести event flow к единой модели

- **Priority:** P1
- **Labels:** `architecture`, `reliability`, `tech-debt`
- **Problem:** AUD-017.
- **Solution:**
  1. Вернуть `GenerationSucceeded` в `generation.worker`.
  2. Worker: DB status → событие → publish.
  3. Убрать `FeedbackStates`, `get_evaluation_keyboard`, `EVALUATION_PROMPT_TEXT`, `FSMContext`, `StorageKey`.
  4. TaskIQ adapter для события.
  5. `handle_generation_succeeded_event()` в evaluation domain.
  6. Handler читает через `context.state`, проверяет `gen_id`, ставит FSM, отправляет prompt + keyboard.
  7. Единый flow для `GenerationFailed`.
- **Tests:** Публикация события; отсутствие FSM-мутаций в worker; JSON deserialization; устаревший `gen_id`; integration flow `RunGeneration → GenerationSucceeded → handler`.
- **DoD:**
  - [ ] `generation.worker.py` без `FeedbackStates`, `get_evaluation_keyboard`, `EVALUATION_PROMPT_TEXT`, `FSMContext`, `StorageKey`.
  - [ ] Успех → `GenerationSucceeded`.
  - [ ] Ошибка → `GenerationFailed(stage="generation")`.
  - [ ] Handler в evaluation domain.
  - [ ] Handler использует `TelegramPort` и `context.state`.
  - [ ] Callbacks evaluation/feedback работают.
  - [ ] Integration test полного flow.
  - [ ] `ARCHITECTURE.md` обновлён.
- **Estimate:** 2–3 дня.
- **Dependencies:** TASK-001.
- **Risks:** Событие после `send_audio` не даёт exactly-once. Handler должен быть идемпотентен.

#### TASK-010 — Durable audio и отдельная доставка

- **Priority:** P1
- **Labels:** `data-loss`, `money-loss`, `reliability`
- **Problem:** AUD-004.
- **Solution:**
  1. Persistent artifact в volume/object storage.
  2. DB reference + size + checksum.
  3. Доставка отдельной повторяемой стадией.
  4. Telegram message ID после успеха.
  5. Retention policy.
- **Tests:** Сбой Telegram / PostgreSQL в каждой точке между генерацией, сохранением, отправкой.
- **DoD:**
  - [ ] Результат переживает рестарт worker.
  - [ ] Telegram failure не требует новой генерации.
  - [ ] Проверены сбои между стадиями.
  - [ ] Retention policy документирована.
- **Estimate:** 3–5 дней.
- **Dependencies:** TASK-007.

#### TASK-011 — Завершение и recovery generation state

- **Priority:** P1
- **Labels:** `bug`, `reliability`
- **Problem:** AUD-005.
- **Solution:**
  1. Terminal transition с условием по `attempt_id`.
  2. FSM update только при совпадении `gen_id`.
  3. Recovery зависших операций.
- **Tests:** Ошибка API, ошибка notify, ошибка Telegram, отмена, устаревший worker.
- **DoD:**
  - [ ] Определённый отказ не оставляет `PENDING`.
  - [ ] Ошибка уведомления не препятствует DB transition.
  - [ ] Старый worker не изменяет новый сценарий.
- **Estimate:** 1–2 дня.
- **Dependencies:** TASK-007, TASK-008.

#### TASK-012 — Привязать feedback к конкретному `gen_id`

- **Priority:** P1
- **Labels:** `bug`, `data-loss`
- **Problem:** AUD-007.
- **Solution:**
  1. `gen_id` в callback/FSM.
  2. Ownership check.
  3. Upsert с обновлением только переданных полей.
  4. Явная ошибка сохранения вместо swallow.
- **Tests:** Старый callback; чужой `gen_id`; commit failure; конкурентные upserts.
- **DoD:**
  - [ ] Старый callback изменяет только указанный трек либо отклоняется.
  - [ ] DB failure не трактуется как успех.
  - [ ] Конкурентность проверена на PostgreSQL.
- **Estimate:** 1–2 дня.

#### TASK-013 — Исправить SSE/audio assembly

- **Priority:** P1
- **Labels:** `bug`, `data-loss`, `reliability`
- **Problem:** AUD-010.
- **Solution:**
  1. Убрать content-based `startswith` detection.
  2. Валидировать terminal condition.
  3. Лимиты входных данных до JSON parsing.
- **Tests:** Повторяющиеся deltas; EOF без terminal event; error event после части аудио; malformed JSON; oversized line.
- **DoD:**
  - [ ] Повторяющиеся deltas сохраняются полностью.
  - [ ] Оборванный stream не выдаёт success.
  - [ ] Oversized event ограничен до JSON parsing.
- **Estimate:** 1–2 дня.

#### TASK-014 — Устранить TOCTOU в Redis lock

- **Priority:** P1
- **Labels:** `bug`, `reliability`
- **Problem:** AUD-009.
- **Solution:** Lua compare-and-delete. Bounded wait с deadline.
- **Tests:** Истечение между чтением и удалением; чужой токен; потеря Redis.
- **DoD:**
  - [ ] Старый owner не удаляет новый lock.
  - [ ] Ожидание lock имеет deadline.
  - [ ] Тест Lua на реальном изолированном Redis.
- **Estimate:** 0.5–1 день.

#### TASK-015 — Единообразная retry policy

- **Priority:** P1
- **Labels:** `reliability`, `external-api`
- **Problem:** AUD-014.
- **Solution:**
  1. GET credits — bounded retries с deadline.
  2. POST enricher — отдельная policy.
  3. Типизированные ошибки, `Retry-After`.
- **Tests:** `429 → 200`, `503 → 200`, `401` без retry, malformed response.
- **DoD:**
  - [ ] GET retry ограничен числом попыток и deadline.
  - [ ] `401` не повторяется.
  - [ ] Policy POST документирована отдельно.
- **Estimate:** 0.5–1 день.

### P2 — MEDIUM

#### TASK-016 — Достоверность тестов и broker integration

- **Priority:** P2
- **Labels:** `tech-debt`, `reliability`, `dx`
- **Problem:** AUD-012, AUD-015.
- **Solution:**
  1. Исправить worker fixtures.
  2. Contract tests.
  3. Integration с PostgreSQL/Redis/RabbitMQ.
  4. Network deny by default.
  5. Проверить ACK/requeue/DLQ.
- **Tests:** Реальная доставка задачи; poison message; smoke не объявляет инфраструктуру здоровой после skip.
- **DoD:**
  - [ ] Нет патчей отсутствующих symbols.
  - [ ] Проверена реальная доставка задачи.
  - [ ] Проверены ACK/requeue/DLQ.
  - [ ] Логи Ruff, Mypy, Pytest сохранены.
- **Estimate:** 2–4 дня.
- **Dependencies:** `poetry.lock`.

#### TASK-017 — Контрактные тесты Telegram-адаптера и API policies

- **Priority:** P2
- **Labels:** `reliability`, `observability`, `telegram`
- **Problem:** AUD-011 (продолжение), AUD-014.
- **Solution:**
  1. Проверить signature.
  2. Тестировать реальный adapter с fake transport.
  3. Покрыть `FakeTelegramPort` и `AiogramTelegramPort` контрактно.
- **DoD:**
  - [ ] Owner notification проходит без сети через реальный adapter.
  - [ ] `FakeTelegramPort` покрывает send/edit/audio/owner notification.
  - [ ] Есть тест отсутствующего `TelegramPort` → диагностируемая ошибка.
- **Estimate:** 1 день.

### P3 — LOW

#### TASK-018 — Синхронизировать docs, backlog и quality gates

- **Priority:** P3
- **Labels:** `docs`, `dx`, `tech-debt`
- **Problem:** AUD-016.
- **Solution:**
  1. Актуальная System Map.
  2. Доказательства DoD.
  3. Typing baseline.
  4. Согласование Aider instructions.
- **DoD:**
  - [ ] Нет дублированных архитектурных секций.
  - [ ] Нет TASK одновременно в To Do и Done.
  - [ ] Закрытые DoD ссылаются на тесты/миграции.
  - [ ] Mypy baseline и исключения описаны.
  - [ ] Новые задачи внесены в `docs/backlog.md`.
- **Estimate:** 1–2 дня.

---

## 4. Comparison with Existing Backlog

| Existing | Статус | Остаток |
|---|---|---|
| TASK-001 | Частично | Нет конкурентного PostgreSQL-теста; target generation через latest |
| TASK-002 | Частично | Миграция и `EXPLAIN ANALYZE` не предоставлены |
| TASK-003 | Частично | Post-POST economics, durable result |
| TASK-004 | Не покрывает | Не покрывает `context[...]` и несовместимость `notify_owner` → TASK-001, TASK-002 |
| TASK-005 | Требует пересмотра | Retry платного POST → TASK-008 |
| TASK-006 | Частично | `Any`, untyped signatures |
| TASK-007 | Открыта | Плюс дефект одинаковых deltas → TASK-013 |
| TASK-008 | Частично | Polling wiring, drain, `stop_grace_period` → TASK-005 |
| TASK-009 | Не подтверждена | Метрики, alerts |
| TASK-010 | Открыта | Отбор эксплуатационных параметров |
| TASK-011 | Открыта | Structured logs |
| TASK-012 | Не проверена | Config не предоставлен |
| TASK-013 | Устарела | Docs противоречивы → TASK-018 |

Не покрыто ни одной: AUD-011, AUD-017, AUD-018, удаление неиспользуемых broker connections → TASK-001, TASK-002, TASK-003, TASK-009.

---

## 5. Roadmap

### Quick wins

1. TASK-001 — context.state в event handlers.
2. TASK-002 — notify_owner.
3. TASK-003 — Compose cleanup.
4. TASK-004 — закрыть ports.
5. TASK-005 — polling shutdown.
6. TASK-006 — `/credits` FSM.

### Критический путь

```text
TASK-001 (context access)
    ↓
TASK-002 (notify_owner)
    ↓
TASK-003 (Compose cleanup)
    ↓
TASK-009 (unified event flow)
    ↓
TASK-007 (atomic claim)
    ↓
TASK-008 (safe retry)
    ↓
TASK-010 (durable audio)
    ↓
TASK-011 (recovery)
    ↓
TASK-016 (integration tests)