---

kanban-plugin: board

---

## Quick wins

- [ ] AUD-004 — Проверить ownership-safe cleanup Redis
	  - Описание: убедиться, что после перехода на instance-specific Redis keys старый cleanup не удаляет задачи другого экземпляра.
	  - Агенту:
	- Найти все вызовы `clear_active_tasks()`.
	- Проверить, что каждый вызов использует текущий `instance_id`.
	- Проверить отсутствие операций с общим ключом `bot:active_tasks`.
	- Запустить unit-тесты registry и ownership-тесты.
	- Не менять архитектуру instance ownership без отдельного согласования.
	  - DoD:
	- [ ] В production-коде нет обращения к общему ключу `bot:active_tasks`.
	- [ ] Cleanup удаляет только `bot:active_tasks:{current_instance_id}`.
	- [ ] Тест экземпляра A не удаляет ключ экземпляра B.
	- [ ] Тест stale instance cleanup проходит.
	- [ ] В логах присутствует `instance_id`.
- [ ] AUD-040 — Исправить импорт `src.shared`
	  - Описание: `fake_telegram.py` импортирует `src.shared.ports.telegram`, хотя Dockerfile копирует содержимое `src/` в `/app`.
	  - Агенту:
	- В `shared/ports/fake_telegram.py` заменить:
	  ```python
	  from src.shared.ports.telegram import TelegramPort
	  ```
	  на:
	  ```python
	  from shared.ports.telegram import TelegramPort
	  ```
	- Выполнить поиск всех runtime-импортов `from src.`.
	- Проверить импорты из корня проекта и внутри Docker image.
	  - DoD:
	- [ ] В runtime-коде нет ошибочных импортов `from src.`.
	- [ ] `FakeTelegramPort` импортируется из корня проекта.
	- [ ] `FakeTelegramPort` импортируется внутри контейнера.
	- [ ] Тесты worker-а проходят после изменения.
- [ ] AUD-046 — Исправить typo `base_messasges`
	  - Описание: имя модуля `base_messasges.py` содержит опечатку и сохраняет legacy-след.
	  - Агенту:
	- Переименовать модуль в `base_messages.py`.
	- Обновить все импорты и ссылки.
	- Проверить документацию и тесты.
	- Не оставлять compatibility-модуль без необходимости.
	  - DoD:
	- [ ] Модуль называется `base_messages.py`.
	- [ ] Поиск по проекту не находит активных импортов `base_messasges`.
	- [ ] Runtime-код импортирует новый модуль.
	- [ ] Полный тестовый набор проходит.
- [ ] AUD-048 — Очистить и нормализовать Kanban
	  - Описание: текущий Kanban содержит дубликаты AUD-022/AUD-025 и закрытые DoD, которые не подтверждаются текущим кодом.
	  - Агенту:
	- Оставить ровно одну карточку на каждый AUD-ID.
	- Удалить дубли AUD-022 и AUD-025.
	- Переоткрыть AUD-019, AUD-020, AUD-022, AUD-027, AUD-029 и AUD-032.
	- Не ставить `[x]` без конкретного теста, миграции, конфигурации или документации.
	- Проверить согласованность секций `In Progress`, `Done` и `Reopened`.
	  - DoD:
	- [ ] Каждый AUD-ID встречается ровно один раз.
	- [ ] В `Done` нет задач с неподтверждёнными DoD.
	- [ ] Каждый `[x]` имеет проверяемое доказательство.
	- [ ] Все текущие P0/P1 находятся в активных секциях.
	- [ ] Kanban не противоречит коду и документации.


## 🔴 P0 — BLOCKER

- [ ] AUD-033 — Исправить feedback upsert для SQLite и PostgreSQL
	  - Описание: `feedback/service.py` безусловно использует PostgreSQL-specific `pg_insert`, хотя тестовая БД работает на SQLite. Это регрессия AUD-001 и блокер CI.
	  - Затрагивает:
	- `feedback/service.py`
	- feedback unit tests
	- feedback concurrency tests
	- AUD-019
	  - Агенту:
	1. Открыть `feedback/service.py`.
	2. Проверить текущий dialect через `session.bind.dialect.name`.
	3. Реализовать один из вариантов:
	   - вернуть dialect-independent `SELECT → INSERT/UPDATE` с обработкой гонки;
	   - использовать отдельную SQLite- и PostgreSQL-ветки;
	   - использовать portable SQLAlchemy API, если он покрывает требуемый контракт.
	4. Сохранить ownership-проверку:
	   - `Generation.id == gen_id`;
	   - `Generation.user_id == user_id`;
	   - `Generation.status == SUCCESS`.
	5. Сохранить правило частичного обновления:
	   - `None` не затирает существующий `feedback`;
	   - `None` не затирает существующий `is_liked`.
	6. Обработать конкурентный `IntegrityError`, если выбранная стратегия его допускает.
	7. Не использовать PostgreSQL insert при SQLite-сессии.
	  - Тесты:
	- тест обычного feedback save на SQLite;
	- тест обновления только `is_liked`;
	- тест обновления только `feedback`;
	- тест запрета доступа к чужой генерации;
	- два параллельных вызова `save_feedback` на SQLite;
	- PostgreSQL integration test для atomic upsert.
	  - DoD:
	- [ ] `test_services_feedback.py` проходит на SQLite.
	- [ ] SQLite не компилирует PostgreSQL-specific `Insert`.
	- [ ] PostgreSQL upsert проходит integration test.
	- [ ] Для одной генерации существует не более одной feedback-записи.
	- [ ] Параллельные callback-запросы не приводят к `FeedbackSaveError`.
	- [ ] `None` не затирает сохранённые значения.
	- [ ] AUD-019 не отмечен выполненным до прохождения SQLite и PostgreSQL тестов.
- [ ] AUD-034 — Сделать migration job обязательным gate деплоя
	  - Описание: `migrate` запускает Alembic, но `bot` и workers не зависят от успешного завершения миграции. Приложение может стартовать параллельно с обновлением схемы.
	  - Затрагивает:
	- `docker-compose.yml`
	- `ARCHITECTURE.md`
	- deployment runbook
	- CI/deploy scripts
	  - Агенту:
	1. Выбрать и зафиксировать единый deployment protocol.
	2. Предпочтительный вариант:
	   - добавить migration profile;
	   - запускать миграцию отдельным контролируемым шагом;
	   - запускать bot/workers только после успешного exit code migration job.
	3. Если используется Compose dependency gate:
	   - добавить зависимость на `migrate`;
	   - использовать `condition: service_completed_successfully`;
	   - проверить поведение при активном и неактивном profile.
	4. Проверить конфигурацию командой:
	   ```bash
	   docker compose config
	   ```
	5. Проверить, что добавленный profile действительно активируется в deploy-команде.
	6. Обновить `ARCHITECTURE.md`, чтобы он описывал фактический протокол.
	7. Добавить smoke-сценарий с намеренно падающей миграцией.
	  - Важно:
	- Нельзя добавить `profiles: ["migration"]`, но оставить `depends_on` на сервис, который не включён в активный profile.
	- Нельзя считать healthcheck PostgreSQL эквивалентом успешного применения Alembic.
	  - DoD:
	- [ ] Migration protocol выбран и записан в runbook.
	- [ ] Bot не стартует до успешного завершения миграции.
	- [ ] Generation worker не стартует до успешного завершения миграции.
	- [ ] Enricher worker не стартует до успешного завершения миграции.
	- [ ] Ошибка Alembic возвращает ненулевой код.
	- [ ] Ошибка миграции блокирует успешный deploy.
	- [ ] `docker compose config` проходит.
	- [ ] Документация соответствует фактическому Compose-поведению.


## 🟠 P1 — HIGH

- [ ] AUD-035 — Добавить корректный статус `DeliveryStatus.SKIPPED`
	  - Описание: документация и метрики используют `SKIPPED`, но enum его не содержит. Сейчас пользовательская отмена delivery записывается как `FAILED`.
	  - Затрагивает:
	- `domains/generation/models.py`
	- `domains/generation/worker.py`
	- Alembic migration
	- metrics
	- cancellation documentation
	  - Агенту:
	1. Добавить:
	   ```python
	   SKIPPED = "skipped"
	   ```
	2. Проверить длину SQLAlchemy Enum.
	3. Создать миграцию, если фактическая схема БД требует изменения.
	4. В ветке delivery cancellation записывать `SKIPPED`.
	5. В ветке ошибки Telegram записывать `FAILED`.
	6. Проверить, что `Generation.status` остаётся `SUCCESS` при skip delivery.
	7. Синхронизировать документацию и метрику.
	  - DoD:
	- [ ] `DeliveryStatus.SKIPPED` существует в Python enum.
	- [ ] PostgreSQL migration применяется без ошибки.
	- [ ] Cancel delivery записывает `SKIPPED`.
	- [ ] Telegram/network error записывает `FAILED`.
	- [ ] `DELIVERY_TOTAL` использует значения enum без расхождений.
	- [ ] Есть тест skip delivery.
	- [ ] Есть тест failed delivery.
	- [ ] `ARCHITECTURE.md` и `cancellation_flow.md` соответствуют коду.
- [ ] AUD-036 — Реализовать delivery lease и восстановление зависших задач
	  - Описание: delivery claim переводит `NOT_DELIVERED` в `IN_PROGRESS`, но полноценный lease expiry и recovery не подтверждены.
	  - Затрагивает:
	- `Generation` model;
	- delivery migration;
	- `claim_delivery_atomic`;
	- delivery worker;
	- retry tests.
	  - Агенту:
	1. Выбрать модель lease:
	   - `delivery_claimed_at`;
	   - `delivery_lease_until`;
	   - либо отдельная delivery table.
	2. Зафиксировать TTL lease в конфигурации.
	3. Изменить claim так, чтобы он принимал:
	   - `NOT_DELIVERED`;
	   - `IN_PROGRESS` с истёкшим lease.
	4. При новом claim генерировать новый `delivery_attempt_id`.
	5. Убедиться, что активный lease не перехватывается другим worker.
	6. Добавить recovery path для зависшего `IN_PROGRESS`.
	7. Документировать окно неопределённости между успешным ответом Telegram и записью `DELIVERED`.
	  - DoD:
	- [ ] Lease хранится персистентно.
	- [ ] Активный lease защищён от второго worker.
	- [ ] Истёкший lease можно перехватить.
	- [ ] Старый `delivery_attempt_id` не может изменить состояние.
	- [ ] Есть тест падения worker после claim.
	- [ ] Есть тест повторного claim после expiry.
	- [ ] Есть метрика stale/expired delivery attempt.
	- [ ] Возможный duplicate send после внешнего side effect описан явно.
- [ ] AUD-037 — Устранить TOCTOU в `cmd_cancel`
	  - Описание: `/cancel` отдельно читает generation status и затем устанавливает только один Redis cancel-token. Между операциями worker может перейти в другую фазу.
	  - Затрагивает:
	- `domains/base/handlers.py`
	- Redis cancellation helpers
	- generation worker
	- delivery worker
	  - Агенту:
	1. Не полагаться на устаревший результат отдельного `SELECT`.
	2. Выбрать стратегию:
	   - устанавливать оба токена отмены;
	   - либо выполнять атомарную DB/Lua-операцию.
	3. Если устанавливаются оба токена:
	   - generation worker проверяет generation-token;
	   - delivery worker проверяет delivery-token;
	   - каждый обработанный токен очищается;
	   - TTL остаётся ограниченным.
	4. Проверить поведение при состояниях:
	   - `PENDING`;
	   - `PROCESSING`;
	   - `SUCCESS`;
	   - `FAILED`;
	   - `CANCELLED`.
	5. Не разрешать `/cancel` переводить `SUCCESS` обратно в generation failure/cancel.
	  - Тесты:
	- status `PENDING`, затем worker переводит запись в `PROCESSING`;
	- status `PROCESSING`, затем worker переводит запись в `SUCCESS`;
	- cancel непосредственно перед API call;
	- cancel после сохранения MP3;
	- cancel перед Telegram send.
	  - DoD:
	- [ ] Race `PENDING → PROCESSING` покрыта тестом.
	- [ ] Race `PROCESSING → SUCCESS` покрыта тестом.
	- [ ] Race `SUCCESS → delivery` покрыта тестом.
	- [ ] После подтверждённой отмены аудио не отправляется неожиданно.
	- [ ] `Generation.status=SUCCESS` не меняется из-за delivery skip.
	- [ ] Cancel tokens имеют TTL и очищаются после обработки.
- [ ] AUD-038 — Подключить или удалить dead orchestration flow
	  - Описание: `pipeline_handlers.py` содержит `_acquire_slot`, `_cleanup_cancelled`, `_handle_failure`, `_deliver_result`, `_release_slot`, `_is_actual_gen` и `_make_progress_reporter`, но production `generate_and_send()` работает по другому пути.
	  - Агенту:
	1. Проверить все вызовы перечисленных функций.
	2. Проверить, вызывается ли `register_active_task()` в реальном production flow.
	3. Выбрать один вариант:
	   - подключить `_acquire_slot()` и связанные функции;
	   - удалить dead orchestration code и локальную asyncio cancellation;
	   - оставить только минимальные registry-функции, если они нужны UI-состоянию.
	4. Не подключать старый flow частично.
	5. Синхронизировать `/cancel`, registry и документацию.
	  - Рекомендация:
	- Если генерация уже передаётся TaskIQ, предпочтительнее удалить неиспользуемую локальную orchestration-ветку и оставить DB/Redis cancellation.
	  - DoD:
	- [ ] У каждой оставленной функции есть production caller.
	- [ ] `register_active_task()` либо вызывается в реальном flow, либо удалён.
	- [ ] `get_active_task()` не используется как источник ложной гарантии отмены.
	- [ ] `/cancel` тестирует реально работающий механизм.
	- [ ] В документации нет обещания неработающей локальной asyncio cancellation.
	- [ ] Нет dead-функций, обнаруживаемых статическим поиском.
- [ ] AUD-039 — Определить и реализовать retry policy для failed delivery
	  - Описание: после `FAILED` текущий claim не позволяет повторно забрать доставку, если нет отдельного retry flow.
	  - Агенту:
	1. Зафиксировать, является ли `FAILED`:
	   - terminal state;
	   - автоматически retryable;
	   - retryable только через ручную команду.
	2. Если retry разрешён:
	   - добавить счётчик попыток;
	   - добавить backoff;
	   - ограничить число повторов;
	   - повторять только delivery, не generation.
	3. Согласовать retry с lease recovery из AUD-036.
	4. Отделить `SKIPPED` от retryable `FAILED`.
	  - DoD:
	- [ ] Retry policy описана в документации.
	- [ ] Есть тест `Telegram failure → retry`.
	- [ ] Retry не вызывает OpenRouter повторно.
	- [ ] Количество retry ограничено.
	- [ ] Backoff измерим и тестируем.
	- [ ] Метрики различают initial delivery и retry delivery.
	- [ ] Повторная доставка не возможна для `SKIPPED`, если это запрещено политикой.
- [ ] AUD-020 — Довести persistent idempotency Telegram delivery
	  - Описание: atomic claim появился, но delivery contract не завершён без `SKIPPED`, lease recovery и retry policy.
	  - Агенту:
	- Синхронизировать работу AUD-035, AUD-036 и AUD-039.
	- Пересмотреть state machine:
	  ```text
	  NOT_DELIVERED → IN_PROGRESS → DELIVERED
	                            ↘ FAILED
	                            ↘ SKIPPED
	  ```
	- Проверить stale attempt protection.
	- Обновить integration tests и документацию.
	  - DoD:
	- [ ] Конкурентные worker не отправляют один результат одновременно.
	- [ ] `DELIVERED` не перезаписывается повторной обработкой.
	- [ ] `SKIPPED` и `FAILED` различаются.
	- [ ] Зависший `IN_PROGRESS` восстанавливается.
	- [ ] Retry policy реализована или явно запрещена.
	- [ ] Все переходы state machine покрыты тестами.
	- [ ] Документация не обещает exactly-once delivery через внешний Telegram API.


## 🟡 P2 — MEDIUM

- [ ] AUD-041 — Удалить dead `_publish_generation_failed`
	  - Описание: `enricher/worker.py` содержит `_publish_generation_failed`, которая не вызывается, но требует `generation_broker`.
	  - Агенту:
	- Найти все вызовы `_publish_generation_failed`.
	- Если вызовов нет — удалить функцию.
	- Удалить `generation_broker` из импорта, если он больше не нужен.
	- Проверить фактический маршрут `GenerationFailed(stage="enrichment")`.
	  - DoD:
	- [ ] Нет неиспользуемой `_publish_generation_failed`.
	- [ ] Нет лишней зависимости enricher worker от generation broker.
	- [ ] Ошибка enrichment публикуется ровно одним маршрутом.
	- [ ] Есть тест доставки enrichment failure event.
- [ ] AUD-042 — Убрать дублирование enrichment failure handling
	  - Описание: `generation/handlers.py` содержит ветку `event.stage == "enrichment"`, хотя canonical handler находится в `enricher/handlers.py`.
	  - Агенту:
	- Определить владельца обработки `stage="enrichment"`.
	- Оставить обработку enrichment только в enricher domain.
	- В generation handler оставить только стадии generation/delivery, если это соответствует контракту.
	- Обновить event routing tests.
	  - DoD:
	- [ ] Для каждого `GenerationFailed.stage` определён один canonical handler.
	- [ ] Нет недостижимой ветки `stage="enrichment"`.
	- [ ] Enrichment retry/fallback работает через enricher handler.
	- [ ] Generation failure handler не меняет enrichment FSM.
	- [ ] Документация соответствует реальному маршруту.
- [ ] AUD-043 — Синхронизировать delivery documentation с кодом
	  - Описание: `ARCHITECTURE.md` и `cancellation_flow.md` описывают `SKIPPED` и lease раньше, чем они реализованы.
	  - Агенту:
	- До завершения AUD-035/AUD-036 временно убрать неподтверждённые claims или явно пометить их как target design.
	- После реализации обновить diagrams и state transitions.
	- Проверить все упоминания `SKIPPED`, `lease`, `migration profile`.
	  - DoD:
	- [ ] Документированные enum values совпадают с model.
	- [ ] Документированные transitions совпадают с worker.
	- [ ] Lease описан только после реализации.
	- [ ] Migration protocol совпадает с Compose.
	- [ ] Поиск по документации не находит устаревших имён и переходов.
- [ ] AUD-044 — Сделать real Redis profile обязательным в CI
	  - Описание: `test_redis_lock_real.py` делает `pytest.skip`, если Redis недоступен. Это допустимо локально, но не доказывает, что CI требует Redis.
	  - Агенту:
	1. Найти или добавить CI infrastructure profile.
	2. Явно поднять Redis перед `redis_real` tests.
	3. Разделить локальный и CI режим:
	   - local без Redis → понятный skip;
	   - CI без Redis → failure.
	4. Проверить настоящий:
	   - `SET NX PX`;
	   - mutual exclusion;
	   - compare-and-delete Lua script;
	   - expiration;
	   - reacquisition после expiry.
	5. Изолировать тестовую DB index и вызвать cleanup после тестов.
	  - DoD:
	- [ ] CI явно запускает Redis.
	- [ ] Redis недоступен в CI → тесты падают.
	- [ ] Локальный skip документирован.
	- [ ] Реальный Redis подтверждает mutual exclusion.
	- [ ] Чужой token не удаляет lock.
	- [ ] Expired lock можно захватить повторно.
	- [ ] CI-лог различает pass/fail/skip.
- [ ] AUD-045 — Довести RabbitMQ smoke до инфраструктурного E2E
	  - Описание: hardcoded credentials убраны, но fallback строит localhost URL, а недоступность RabbitMQ приводит к `pytest.skip`.
	  - Агенту:
	- Разделить тесты:
	  - InMemoryBroker unit test;
	  - RabbitMQ connection smoke;
	  - RabbitMQ worker end-to-end smoke.
	- Передавать URL через отдельный test profile.
	- Убрать неявный fallback в CI.
	- Поднять RabbitMQ в CI infrastructure profile.
	- Проверить, что реальный worker получает и выполняет тестовую задачу.
	- Оставить skip только в локальном профиле.
	  - DoD:
	- [ ] В CI RabbitMQ запускается явно.
	- [ ] Недоступный RabbitMQ в CI приводит к failure.
	- [ ] В тестах нет production credentials.
	- [ ] URL берётся из test configuration.
	- [ ] Connection smoke проходит на реальном RabbitMQ.
	- [ ] Worker E2E smoke подтверждает выполнение задачи.
	- [ ] InMemoryBroker тесты отделены от real infrastructure tests.
- [ ] AUD-047 — Проверить неиспользуемые аргументы `_enrich_and_present`
	  - Описание: `_enrich_and_present` принимает `status` и `enrich_id`, но в доступном ревью указано, что они не используются.
	  - Агенту:
	- Просмотреть полный body функции.
	- Проверить, используется ли `enrich_id` для защиты от stale enrichment result.
	- Проверить, используется ли `status` для редактирования Telegram-сообщения.
	- Если параметры не нужны — удалить их из сигнатуры и всех вызовов.
	- Если нужны — добавить явные проверки актуальности FSM.
	  - DoD:
	- [ ] Каждый аргумент функции используется или удалён.
	- [ ] Повторный enrichment не затирает более новый flow.
	- [ ] Есть тест stale `enrich_id`.
	- [ ] Есть тест успешного редактирования status message.
	- [ ] Нет misleading docstring.
- [ ] AUD-049 — Исправить создание `OperationalError` в тесте
	  - Описание: тест использует `OperationalError("DB connection lost", None, None)`. Конструкция работает, но третий аргумент должен быть реальным исходным exception.
	  - Агенту:
	- В `test_approve_db_error_blocks_generation` создать настоящий `orig`:
	  ```python
	  original_error = ConnectionError("DB connection lost")
	  error = OperationalError("DB operation failed", {}, original_error)
	  ```
	- Проверить, что тест проверяет именно поведение приложения, а не внутреннюю сигнатуру SQLAlchemy.
	- Не использовать deprecated/неинформативный `orig=None`, если это не требуется сценарием.
	  - DoD:
	- [ ] Тест использует реальный `orig` exception.
	- [ ] Тест проходит на SQLAlchemy 2.x.
	- [ ] Ошибка БД не запускает generation flow.
	- [ ] Поведение обработчика остаётся проверяемым.


## 🟢 P3 — LOW

- [ ] AUD-016 — Зафиксировать окончательные границы aiogram dependency
	  - Описание: architecture tests есть, но нужно окончательно закрепить, какие Telegram flow contracts имеют право принимать `Message` и `FSMContext`.
	  - Агенту:
	- Разделить:
	  - Telegram handlers;
	  - Telegram flow contracts;
	  - domain services;
	  - TelegramPort adapters.
	- Проверить `domains/*/service.py`, `workers/*`, `core/*`.
	- Добавить или расширить architecture test на запрещённые imports.
	- Не добавлять новые абстракции только ради формального DDD.
	  - DoD:
	- [ ] Разрешённые aiogram imports перечислены в документации.
	- [ ] Domain services не принимают `Message` и `FSMContext`.
	- [ ] Workers используют `TelegramPort`.
	- [ ] Architecture test падает на запрещённом импорте.
	- [ ] Имена Protocol отражают реальную ответственность.
- [ ] AUD-032 — Повторно синхронизировать architectural claims
	  - Описание: предыдущий cleanup был отмечен выполненным, но новые изменения снова создали drift между docs и code.
	  - Агенту:
	- Сверить `ARCHITECTURE.md` с:
	  - model enums;
	  - generation worker;
	  - delivery worker;
	  - cancellation handler;
	  - Compose;
	  - Redis registry;
	  - event routing.
	- Удалить заявления о гарантиях, которые не реализованы.
	- После завершения P0/P1 обновить диаграммы.
	  - DoD:
	- [ ] Каждый компонент в диаграмме существует или отмечен как conceptual.
	- [ ] `SKIPPED` описан только после реализации.
	- [ ] Delivery idempotency описана с учётом lease и внешнего Telegram API.
	- [ ] Cancellation flow отражает реальные race guarantees.
	- [ ] Migration protocol совпадает с deploy configuration.
	- [ ] Все ссылки на handlers, workers и services проверены.


## In Progress

- [ ] AUD-033 — Feedback upsert compatibility
- [ ] AUD-034 — Migration deployment gate
- [ ] AUD-035 — DeliveryStatus.SKIPPED
- [ ] AUD-037 — Cancellation TOCTOU


## Done

- [x] AUD-021 — Атомарное сохранение MP3 и проверка checksum
- [x] AUD-023 — Базовый ownership-safe Redis cleanup
- [x] AUD-024 — Architecture tests для dependency rules
- [x] AUD-030 — Проверка прав audio volume
- [x] AUD-031 — Базовые generation/delivery metrics
- [x] AUD-013 — Typed callback parser
- [x] AUD-014 — Разделение `attempt_id` и `task_id`
- [x] AUD-017 — Основная зачистка legacy-импортов


## Reopened / Partially done

- [ ] AUD-001 — Feedback dialect compatibility
	  - Причина переоткрытия: новый `pg_insert` ломает SQLite.
	  - Закрывается через AUD-033.
- [ ] AUD-019 — Feedback upsert concurrency
	  - Причина переоткрытия: DoD «тесты проходят на SQLite» не подтверждён.
	  - Закрывается через AUD-033.
- [ ] AUD-020 — Persistent delivery idempotency
	  - Причина переоткрытия: нет завершённого lease/retry contract.
	  - Закрывается через AUD-035, AUD-036 и AUD-039.
- [ ] AUD-022 — Разделение generation и delivery cancellation
	  - Причина переоткрытия: `SKIPPED` отсутствует, `cmd_cancel` имеет TOCTOU race.
	  - Закрывается через AUD-035 и AUD-037.
- [ ] AUD-026 — RabbitMQ smoke tests
	  - Причина переоткрытия: fallback localhost и skip при отсутствии брокера.
	  - Закрывается через AUD-045.
- [ ] AUD-027 — Real Redis integration profile
	  - Причина переоткрытия: CI-поднятие Redis не доказано, тест может silently skip.
	  - Закрывается через AUD-044.
- [ ] AUD-029 — Migration deployment protocol
	  - Причина переоткрытия: migration job не гейтит bot/workers.
	  - Закрывается через AUD-034.
- [ ] AUD-032 — Architectural claims
	  - Причина переоткрытия: документация снова расходится с enum, delivery worker и Compose.
	  - Закрывается после AUD-034, AUD-035, AUD-036 и AUD-037.




%% kanban:settings
```
{"kanban-plugin":"board","list-collapse":[true,true,true,true,false,false,false,false],"show-checkboxes":false,"move-tags":true,"tag-action":"obsidian"}
```
%%