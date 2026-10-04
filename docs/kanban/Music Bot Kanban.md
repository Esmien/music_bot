---

kanban-plugin: board

---

## Quick wins



## 🔴 P0 — BLOCKER



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
- [ ] - [ ] AUD-044 — Верифицировать CI-профиль Redis для AUD-027 либо снять чекбокс
		  - Описание: AUD-027 в разделе Done, чекбокс «[x] CI явно поднимает Redis для профиля» отмечен, но в репозитории нет CI-конфигов (`.github/workflows`, `.gitlab-ci.yml`, `Makefile`), а `test_redis_lock_real.py` делает `pytest.skip`, если Redis недоступен. «Skip при отсутствии» ≠ «CI требует Redis».
		  - Затрагивает:
		- `tests/` (CI-конфиг или Makefile)
		- `docs/kanban/Music Bot Kanban.md` (AUD-027)
		  - Агенту:
		1. Выбрать вариант:
		   - добавить CI workflow, который поднимает `redis-test` через `docker-compose --profile test up -d` и запускает `pytest -m redis_real` без `--ignore`; либо
		   - снять чекбокс «CI явно поднимает Redis» в AUD-027 и переформулировать DoD как «есть возможность ручного запуска с реальным Redis через `docker-compose --profile test`».
		2. Если вариант с CI: убедиться, что `pytest.skip` в `real_redis_connections` не срабатывает в CI-контексте (например, через env-переменную `REQUIRE_REDIS=1`, которая превращает skip в fail).
		3. Добавить в `pyproject.toml` маркер `redis_real` в `markers` (если ещё нет).
		  - DoD:
		- [ ] Либо CI-конфиг поднимает Redis и тест не скипается, либо DoD AUD-027 сформулирован честно.
		- [ ] При `REQUIRE_REDIS=1` недоступность Redis приводит к `pytest.fail`, а не `skip`.
		- [ ] `pytest -m redis_real` локально работает с `docker-compose --profile test up -d`.
		- [ ] Канбан не содержит ложных чекбоксов.


## 🟡 P2 — MEDIUM

- [ ] - [ ] AUD-045 — Исправить `chat_id` в `handle_prompt_retry` при callback
		  - Описание: в `domains/enricher/handlers.py::handle_prompt_retry` при вызове `_enrich_and_present` передаётся `chat_id=callback.from_user.id`. Для callback-запросов это user_id, а не chat_id. В приватных чатах совпадает, в группах (если когда-нибудь включатся) — сломается.
		  - Затрагивает:
		- `domains/enricher/handlers.py`
		  - Агенту:
		1. Заменить `chat_id=callback.from_user.id` на `chat_id=callback.message.chat.id`.
		2. Проверить остальные места, где `callback.from_user.id` передаётся в параметр с именем `chat_id` или `chat`.
		3. Прогнать `grep -rn "chat_id=.*from_user.id" src/domains/`.
		  - DoD:
		- [ ] `handle_prompt_retry` использует `callback.message.chat.id`.
		- [ ] По репозиторию нет аналогичных подмен user_id → chat_id.
		- [ ] `test_retry_first_run_enriches_idea` дополнен проверкой `command.chat_id == expected_chat_id`.
- [ ] - [ ] AUD-046 — Различить транзиентные и терминальные ошибки генерации в UX
		  - Описание: `handle_generation_failed_event` при `stage="generation"` очищает FSM (`set_state(None)`) и показывает сырой `event.error_message` без клавиатуры. Пользователь не может повторить генерацию с сохранённым промптом иначе как через `/start` + заново пройти enricher. Транзиентный сбой (обрыв стрима, 503) и терминальный (400, невалидный промпт) обрабатываются одинаково.
		  - Затрагивает:
		- `domains/generation/handlers.py::handle_generation_failed_event`
		- `domains/generation/keyboards.py`
		- `domains/generation/generation_messages.py`
		- `shared/contracts/events.py` (возможно, добавить поле `retryable: bool`)
		  - Агенту:
		1. Не вызывать `set_state(None)` при `stage="generation"` — сохранить prompt, title, gen_id в FSM, чтобы `retry_generation` сработал.
		2. Прикрепить `get_retry_keyboard()` к сообщению об ошибке.
		3. Заменить `text=f"❌ {event.error_message}"` на пользовательский текст из `generation_messages.py` (например, `GENERATION_FAILED_RETRY_HINT`).
		4. Различать retryable / non-retryable ошибки:
		   - retryable: `GenerationStreamError`, `httpx.TimeoutException`, `GenerationAPIError(status_code in (503, 429))`;
		   - non-retryable: `GenerationAPIError(4xx)`, `GenerationConfigurationError`, `GenerationAudioMissingError` (повтор вряд ли поможет).
		5. Если non-retryable — очистить FSM и показать меню; если retryable — оставить FSM и показать клавиатуру.
		6. Опционально: добавить в `GenerationFailed` поле `retryable: bool = True`, заполнять в `_handle_generation_failure`.
		  - DoD:
		- [ ] При транзиентном сбое `generating=False`, FSM сохраняет prompt/title/gen_id, пользователь видит кнопку «Попробовать снова».
		- [ ] `retry_generation` после stream failure создаёт новую запись `Generation` и не проходит через enricher.
		- [ ] При терминальном сбое FSM очищается, показывается меню.
		- [ ] Тексты сообщений не содержат `str(exception)`.
		- [ ] Есть тест: `GenerationStreamError` → FSM сохранён, keyboard retry.
		- [ ] Есть тест: `GenerationAPIError(400)` → FSM очищен, keyboard main.
		- [ ] AUD-040 пересмотрен: UX-задача решена здесь, а не через retry в HTTP-клиенте.


## 🟢 P3 — LOW

- [ ] AUD-033 — Feedback upsert compatibility
- [x] AUD-034 — Migration deployment gate
- [ ] AUD-035 — DeliveryStatus.SKIPPED
- [ ] AUD-037 — Cancellation TOCTOU
- [ ] - [ ] AUD-047 — Убрать опечатку `base_messasges.py`
		  - Описание: модуль `src/domains/base/base_messasges.py` имеет три «s» в имени. При закрытом AUD-017 («финальная зачистка legacy») это странно и затрудняет поиск по коду.
		  - Затрагивает:
		- `src/domains/base/base_messasges.py` → переименовать в `base_messages.py`
		- все импорты (`domains/base/handlers.py`, тесты)
		  - Агенту:
		1. `git mv src/domains/base/base_messasges.py src/domains/base/base_messages.py`.
		2. Обновить импорты: `from domains.base import base_messasges` → `from domains.base import base_messages`.
		3. Обновить обращения `base_messasges.START_...` → `base_messages.START_...`.
		4. `grep -rn "base_messasges" src/ tests/`.
		  - DoD:
		- [ ] Файл переименован.
		- [ ] `grep -rn "base_messasges" src/ tests/` не находит совпадений.
		- [ ] Тесты проходят.
		- [ ] Ruff проходит.
- [ ] - [ ] AUD-048 — Дедуплицировать AUD-022, AUD-025 и синхронизировать чекбоксы
		  - Описание: в канбане остались две записи `AUD-022` (`[x]` дважды) и две записи `AUD-025` (`[x]` дважды). Дополнительно: `AUD-020` имеет заголовок `[ ]`, но все DoD `[x]` — противоречие. `AUD-027` содержит DoD `[x]`, не подтверждённые кодом (см. AUD-044).
		  - Затрагивает:
		- `docs/kanban/Music Bot Kanban.md`
		  - Агенту:
		1. Удалить дубликат `AUD-022`, оставить один в `Done` (или перенести в `P1` с раскрытыми DoD, если работа не завершена).
		2. Удалить дубликат `AUD-025`, оставить один в `Done`.
		3. `AUD-020` перенести в `P0` или `P1` с заголовком `[ ]` и синхронизировать чекбоксы DoD: то, что реально закрыто — оставить `[x]`, остальное — `[ ]`.
		4. Учесть AUD-035/036/039 при пересмотре AUD-020: если они закрывают часть DoD, оставить их отдельными задачами, а в AUD-020 явно сослаться.
		5. `AUD-027` — по результату AUD-044 либо оставить в `Done`, либо вернуть в `P1`.
		6. Проверить остальные закрытые таски на ложные чекбоксы (например, `AUD-040` — см. AUD-041).
		  - DoD:
		- [ ] В канбане нет дубликатов AUD-022 и AUD-025.
		- [ ] `AUD-020` находится в одном разделе (не в двух).
		- [ ] Чекбоксы DoD соответствуют реальному статусу кода.
		- [ ] Каждый `[x]` подтверждён либо тестом, либо кодом, либо явной ссылкой.
		- [ ] Нет задач с заголовком `[ ]`, у которых все DoD — `[x]`.


## In Progress



## Done

- [x] AUD-043 — Устранить утечку `_user_lock_events` в `user_generation_lock`
	  - Описание: `_user_lock_events: dict[int, asyncio.Event]` в `domains/generation/service.py` пополняется через `setdefault` на каждый новый `user_id` и никогда не очищается. Для долгоживущего процесса — медленная утечка памяти на уникальных пользователей. Дополнительно: `asyncio.Event`, созданный в одном event loop, может переиспользоваться из другого (актуально для тестов с pytest-asyncio).
	  - Затрагивает:
	- `domains/generation/service.py::user_generation_lock`
	- `src/domains/generation/tests/unit/test_redis_lock.py`
	  - Агенту:
	1. Выбрать способ ограниченного хранения (один из):
	   - `WeakValueDictionary[int, asyncio.Event]`;
	   - `dict.pop(user_id, None)` в `finally` после `event.set()`, если лок действительно освобождён;
	   - `contextvars`-based event.
	2. Убедиться, что очистка не ломает сценарий «второй воркер ждёт пробуждения» — event нужен до момента, когда второй процесс увидел `.set()`.
	3. Проверить, что после освобождения лока и `srem` ключа повторный захват того же `user_id` создаёт свежий event.
	4. Добавить тест: два последовательных захвата одного `user_id` не оставляют мусора в `_user_lock_events`.
	  - DoD:
	- [x] После освобождения лока запись в `_user_lock_events` удаляется или event переиспользуется безопасно.
	- [x] Есть тест на отсутствие роста словаря при N последовательных захватах.
	- [x] Существующие тесты Redis-лока проходят без изменений.
	- [x] Комментарий в коде объясняет, почему event нужен, и когда его можно удалять.

- [x] AUD-042 — Исправить импорт `src.shared.ports.telegram` в `fake_telegram.py`
	  - Описание: `src/shared/ports/fake_telegram.py` импортирует `from src.shared.ports.telegram import TelegramPort`. В Dockerfile `COPY src/ ./` и `WORKDIR /app`, поэтому модуль доступен как `shared.ports.telegram`. Импорт работает только при определённом `sys.path` (локально с `PYTHONPATH=src/..`), в контейнере упадёт. Скрытая мина CI/прод.
	  - Затрагивает:
	- `src/shared/ports/fake_telegram.py`
	- проверить все импорты `FakeTelegramPort` в тестах
	  - Агенту:
	1. Заменить `from src.shared.ports.telegram import TelegramPort` на `from shared.ports.telegram import TelegramPort`.
	2. Поиском по репозиторию убедиться, что нигде больше нет `from src.` или `import src.`.
	3. Проверить `src/conftest.py` и `tests/conftest.py` — там есть `from src.conftest import ...`, это осознанное решение для root-level conftest, не трогать.
	  - DoD:
	- [x] `grep -rn "from src\." src/` не находит импортов внутри runtime-кода.
	- [x] `FakeTelegramPort` импортируется в чистом окружении (`docker run ... python -c "from shared.ports.fake_telegram import FakeTelegramPort"`).
	- [x] Все integration-тесты воркеров проходят без дополнительных `sys.path` манипуляций.
	- [x] Ruff проходит.
- [x] AUD-041 — Откатить retry для `GenerationStreamError` в `core/utils/retry.py`
	  - Описание: AUD-040 расширил retry-политику так, что `GenerationStreamError` (обрыв SSE после успешного POST) теперь повторяется. Это нарушает контракт CONVENTIONS.md («повторный запрос запрещён после старта стриминга») и приводит к двойному списанию средств OpenRouter без гарантии результата. UX-задача AUD-040 решалась в другом слое и не требует этой правки.
	  - Затрагивает:
	- `core/utils/retry.py`
	- `domains/generation/tests/unit/test_services_generation.py`
	- `docs/ARCHITECTURE.md` (раздел Retry)
	  - Агенту:
	1. В `is_retryable_error` вернуть `GenerationStreamError → False`.
	2. Удалить комментарий `# DEVIATION: AUD-040 включает GenerationStreamError...`.
	3. В `make_retry_logger` оставить разделение `phase=pre-request | post-request` — оно полезно для логов, но predicate возвращает False для post-request.
	4. Заменить `test_generate_song_real_stream_timeout_retries_and_notifies_ux` на `test_generate_song_real_stream_timeout_does_not_retry_post_request` — проверять `stream_calls == 1`.
	5. Сохранить `test_generate_song_real_stream_failure_exhausts_retries_and_raises` только если он проверяет отсутствие повторов (иначе переписать).
	6. `on_retry` коллбэк оставить — он срабатывает на pre-request сбоях (503, TimeoutException, ConnectError), это безопасно.
	  - DoD:
	- [x] `GenerationStreamError` не повторяется.
	- [x] `is_retryable_error` возвращает True только для `TimeoutException`, `ConnectError`, HTTP 429, HTTP 503.
	- [x] Лог retry сохраняет разметку `phase=pre-request|post-request`.
	- [x] Есть unit-тест: `stream_calls == 1` после `ReadTimeout` в `aiter_lines`.
	- [x] `CONVENTIONS.md` и `ARCHITECTURE.md` соответствуют коду.
	- [x] DoD AUD-040 пересмотрен: чекбокс «проверка флага отмены в `_before_sleep_generation`» остаётся, чекбоксы про retry на post-request снимаются.
- [x] AUD-034 — Сделать migration job обязательным gate деплоя
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
	- [x] Migration protocol выбран и записан в runbook.
	- [x] Bot не стартует до успешного завершения миграции.
	- [x] Generation worker не стартует до успешного завершения миграции.
	- [x] Enricher worker не стартует до успешного завершения миграции.
	- [x] Ошибка Alembic возвращает ненулевой код.
	- [x] Ошибка миграции блокирует успешный deploy.
	- [x] `docker compose config` проходит.
	- [x] Документация соответствует фактическому Compose-поведению.
- [x] AUD-033 — Исправить feedback upsert для SQLite и PostgreSQL
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
	- [x] `test_services_feedback.py` проходит на SQLite.
	- [x] SQLite не компилирует PostgreSQL-specific `Insert`.
	- [x] PostgreSQL upsert проходит integration test.
	- [x] Для одной генерации существует не более одной feedback-записи.
	- [x] Параллельные callback-запросы не приводят к `FeedbackSaveError`.
	- [x] `None` не затирает сохранённые значения.
	- [x] AUD-019 не отмечен выполненным до прохождения SQLite и PostgreSQL тестов.
- [x] AUD-040 — Настроить UX-ретрай для долгих стриминговых генераций (Tenacity/Taskiq)
	  - Описание: При обрыве соединения с OpenRouter (ошибка `terminal [DONE] event not received` или read timeout) Tenacity выполняет тихий ретрай в фоне. Из-за этого пользователь не видит изменений и считает, что бот завис. Необходимо прокидывать статус ретрая в Telegram для обновления UI.
	  - Затрагивает:
	- `domains/generation/worker.py`
	- `domains/generation/service.py` (где настроен Tenacity)
	- `core/utils/retry.py`
	- `ARCHITECTURE.md`
	  - Агенту:
	1. Увеличить `read` и `connect` таймауты HTTP-клиента (например, `httpx.Timeout(read=300.0)`) для долгих запросов генерации аудио.
	2. Добавить кастомный коллбэк (параметр `before_sleep` или `after` в декораторе `@retry` библиотеки Tenacity) в функции, выполняющей запрос.
	3. Выбрать и зафиксировать подход к обновлению UI:
	   - прямой вызов API Telegram из Taskiq-воркера (потребует инициализации бота);
	   - публикация event'а о ретрае в Redis (Pub/Sub), который будет слушать отдельный сервис-апдейтер.
	4. В коллбэке ретрая формировать сообщение для пользователя (например: "Сервер нейросети моргнул, переподключаюсь... (Попытка N из M)").
	5. Убедиться, что при исчерпании всех попыток Tenacity выбрасывает ошибку в Taskiq, и корректно отрабатывает `handle_generation_failed`.
	6. Добавить проверку флага отмены генерации перед каждой попыткой retry.
	  - DoD:
	- [x] HTTP-клиент использует явно заданные увеличенные таймауты для OpenRouter (connect=30s, read=300s).
	- [ ] При `GenerationStreamError` или `Timeout` UI в Telegram обновляется, информируя пользователя о номере попытки.
	- [x] Подход к пробросу UI-уведомлений из воркера зафиксирован в `ARCHITECTURE.md` с описанием механизма on_retry коллбэка.
	- [ ] Написан unit-тест, проверяющий срабатывание механизма обновления UI при симуляции падения стрима.
	- [x] Окончательное падение всех ретраев переводит статус в `FAILED` и отправляет финальное уведомление пользователю.
	- [x] Проверка флага отмены генерации выполняется в `_before_sleep_generation` перед каждым retry.
- [x] AUD-032 — Удалить устаревшие architectural claims
	  - Описание: часть документации описывает более строгую изоляцию и более сильную идемпотентность, чем реально реализовано.
	  - ТЗ:
	- Сверить `ARCHITECTURE.md` с worker, ports, delivery и cancellation code.
	- Удалить утверждения о полной изоляции от aiogram, если она не соблюдается.
	- Уточнить, что именно является source of truth для generation и delivery.
	- Обновить диаграммы после AUD-020–AUD-024.
	  - DoD:
	- [x] Каждый компонент из диаграммы существует или явно помечен концептуальным.
	- [x] Delivery idempotency описана только в пределах реально реализованного контракта.
	- [x] Cancellation flow соответствует коду.
	- [x] Feedback contract соответствует ORM-модели.
	- [x] Все ссылки в документации проверены.
- [x] AUD-031 — Добавить метрики generation и delivery lifecycle
	  - Описание: текущие логи содержат контекст, но нет агрегируемых метрик для дорогих и долгих операций.
	  - ТЗ:
	- Определить counters для generation success/failed/cancelled.
	- Добавить counters для delivery success/failed/skipped.
	- Добавить latency для OpenRouter, storage и Telegram delivery.
	- Добавить счётчик stale attempt и Redis errors.
	- Документировать минимальный production dashboard.
	  - DoD:
	- [x] Есть метрика количества генераций по финальному статусу.
	- [x] Есть метрика delivery failures.
	- [x] Есть latency generation и delivery.
	- [x] В метриках отсутствуют токены, пользовательские тексты и секреты.
	- [x] Доступен alert на рост generation/delivery failures.
- [x] AUD-029 — Вынести миграции из startup bot-контейнера
	  - Описание: Alembic запускается только при default startup bot-контейнера, а масштабирование bot replicas может привести к конкурирующим миграциям.
	  - ТЗ:
	- Выбрать отдельный migration job или distributed migration lock.
	- Добавить Compose profile для migration job.
	- Убрать зависимость worker startup от запуска Alembic.
	- Проверить failure behavior при неуспешной миграции.
	- Обновить deployment documentation.
	  - DoD:
	- [x] Миграции выполняются отдельным контролируемым шагом.
	- [x] Два bot-контейнера не запускают миграции одновременно без lock.
	- [x] При ошибке миграции deployment не считается успешным.
	- [x] Worker не выполняет Alembic при старте.
	- [x] Deployment runbook содержит команду миграции.
- [x] AUD-028 — Добавить конкурентный тест повторной обработки generation
	  - Описание: текущие тесты проверяют redelivery последовательно, но не доказывают безопасность двух параллельных worker-вызовов.
	  - ТЗ:
	- Запустить два `run_generation_task()` для одного `gen_id`.
	- Использовать barrier/event для синхронизации перед claim.
	- Проверить, что API вызывается один раз.
	- Проверить, что MP3 доставляется согласно delivery policy.
	- Проверить итоговый `attempt_id` и status.
	  - DoD:
	- [x] Два worker-а не выполняют генерацию одновременно.
	- [x] Только один worker получает успешный claim.
	- [x] Stale worker не меняет финальный статус.
	- [x] Количество delivery соответствует policy.
	- [x] Тест проходит на PostgreSQL/Redis integration profile.
- [x] AUD-024 — Зафиксировать границы aiogram-зависимости
	  - Описание: `domain_contracts.py` принимает `aiogram.Message` и `FSMContext`, хотя shared-слой заявлен как абстракция.
	  - ТЗ:
	- Решить, считать ли текущие Protocol application-layer контрактом.
	- Переименовать контракты, если они являются Telegram flow contracts.
	- Документировать разрешённые импорты aiogram.
	- При необходимости ввести DTO для междоменных вызовов.
	- Добавить architecture test на запрещённые импорты.
	  - DoD:
	- [x] Документировано, где разрешён импорт aiogram.
	- [x] Domain services не принимают `Message` и `FSMContext`.
	- [x] Названия Protocol отражают реальную ответственность.
	- [x] Architecture test проверяет dependency rule.
	- [x] Не добавлены необязательные слои только ради формального DDD.
- [ ] AUD-027 — Добавить integration-профиль настоящего Redis
	  - Описание: Redis Lua и lock behavior сейчас частично проверяются через fakeredis и monkeypatch.
	  - ТЗ:
	- Оставить fakeredis для быстрых unit-тестов.
	- Добавить pytest marker `redis_real`.
	- Подключить Redis service в CI/Compose profile.
	- Выполнить настоящий `SET NX PX`.
	- Выполнить настоящий compare-and-delete через `EVAL`.
	- Проверить expiration и повторный захват lock.
	  - DoD:
	- [x] Реальный Redis подтверждает mutual exclusion.
	- [x] Чужой token не удаляет lock.
	- [x] Expired lock захватывается повторно.
	- [x] Lua script выполняется без monkeypatch.
	- [x] CI явно поднимает Redis для профиля.
	- [x] Fakeredis-тесты помечены как unit/in-process.
- [ ] AUD-026 — Синхронизировать RabbitMQ smoke tests с конфигурацией
	  - Описание: тест использует захардкоженный URL `amqp://songai:songai@localhost:5672/`, не совпадающий с Compose-конфигурацией.
	  - ТЗ:
	- Использовать единый `RABBITMQ_URL` из settings.
	- Разделить connection smoke и worker end-to-end smoke.
	- Добавить отдельный Compose/CI profile с RabbitMQ.
	- Подтвердить фактическое выполнение тестовой задачи worker-ом.
	- Удалить захардкоженные production credentials.
	  - DoD:
	- [ ] В smoke-тестах нет захардкоженных RabbitMQ credentials.
	- [ ] URL берётся из тестовой конфигурации.
	- [ ] Реальный RabbitMQ проходит connection smoke.
	- [ ] Реальный worker обрабатывает тестовую задачу.
	- [ ] Недоступный RabbitMQ приводит к явному skip только в локальном профиле.
	- [ ] CI infrastructure profile требует RabbitMQ.
- [x] AUD-023 — Сделать startup cleanup Redis ownership-safe
	  - Описание: `clear_active_tasks()` может удалить общий Redis-set активных задач.
	  - ТЗ:
	- Проверить все вызовы `clear_active_tasks`.
	- Убрать опасное поведение по умолчанию.
	- Привязать активные задачи к `instance_id` или lease.
	- Удалять только ключи текущего экземпляра.
	- Добавить сценарий двух экземпляров при rolling restart.
	- Обработать `RedisError` и частичный cleanup.
	  - DoD:
	- [x] Один экземпляр не удаляет задачи другого.
	- [x] Cleanup всегда ownership-safe, параметр `skip_if_other_instances` удалён.
	- [x] Есть тест overlapping startup.
	- [x] Есть тест stale instance cleanup.
	- [x] Логи содержат `instance_id`, количество найденных и удалённых ключей.
	- [x] Документирован deployment protocol в ARCHITECTURE.md.
- [x] AUD-022 — Разделить отмену генерации и отмену delivery
	  - Описание: один Redis cancel-token используется для разных бизнес-сценариев.
	  - ТЗ:
	- Зафиксировать допустимые переходы generation state.
	- Отделить отмену `PROCESSING` от пропуска delivery после `SUCCESS`.
	- Определить владельца и TTL cancel-token.
	- Гарантировать очистку токена после обработки.
	- Добавить отдельную политику redelivery.
	  - DoD:
	- [x] Отмена до API не вызывает API и переводит generation в `CANCELLED`.
	- [x] Отмена после `SUCCESS` не меняет generation status.
	- [x] Пропуск delivery фиксируется отдельно от cancellation generation.
	- [x] Cancel-token очищается после обработки или его TTL документирован.
	- [x] Есть тесты отмены до claim, во время API, после сохранения MP3 и во время delivery.
	- [x] Stale `attempt_id` не меняет статус и не отправляет результат.
- [x] AUD-022 — Разделить отмену генерации и отмену delivery
- [x] AUD-021 — Сделать сохранение MP3 атомарным и проверять целостность
	  - Описание: `write_bytes()` пишет напрямую в финальный путь `gen_{gen_id}.mp3`.
	  - ТЗ:
	- Создавать временный файл в том же каталоге.
	- Записывать данные во временный файл.
	- Рассчитывать размер и SHA-256 checksum.
	- После успешной записи выполнять atomic rename через `os.replace`.
	- Удалять временный файл при исключении.
	- Перед delivery проверять существование, размер и checksum.
	  - DoD:
	- [x] Финальный путь не появляется до завершения записи.
	- [x] Повреждённый или неполный файл не доставляется.
	- [x] Временные файлы удаляются после успеха и ошибки.
	- [x] Checksum в БД совпадает с фактическим файлом.
	- [x] Есть тест ошибки записи.
	- [x] Есть тест повторного сохранения одного `gen_id`.
- [ ] AUD-020 — Ввести persistent idempotency для Telegram delivery
	  - Описание: повторная обработка `SUCCESS`-генерации может повторно отправить MP3 и повторно опубликовать `GenerationSucceeded`.
	  - ТЗ:
	- Определить delivery state: `NOT_DELIVERED`, `IN_PROGRESS`, `DELIVERED`, `FAILED`.
	- Добавить состояние в модель или отдельную таблицу delivery.
	- Реализовать атомарный claim доставки.
	- Добавить lease/TTL для зависших `IN_PROGRESS`.
	- Не отправлять повторно уже подтверждённую доставку.
	- Отделить ошибку delivery от ошибки generation.
	  - DoD:
	- [x] Два конкурентных worker-а не отправляют один MP3 дважды.
	- [x] Сбой Telegram API не меняет `Generation.status=SUCCESS`.
	- [x] Повторная попытка delivery работает только по документированной политике.
	- [x] Delivery state сохраняется в PostgreSQL.
	- [x] Есть concurrent integration-тест.
	- [x] Есть логирование `gen_id`, delivery state и attempt/lease id.
- [ ] AUD-019 — Сделать feedback upsert безопасным при гонке
	  - Описание: текущая схема `SELECT → INSERT/UPDATE` может завершиться `IntegrityError` при двух одновременных callback-запросах.
	  - ТЗ:
	- Сохранить ownership-проверку `gen_id/user_id`.
	- Выбрать стратегию для PostgreSQL и SQLite.
	- Для PostgreSQL использовать `ON CONFLICT` либо эквивалентный атомарный upsert.
	- Для SQLite использовать совместимую реализацию.
	- Обработать конкурентный `IntegrityError`, если он возможен в выбранной стратегии.
	- Добавить тест двух параллельных вызовов `save_feedback`.
	  - DoD:
	- [x] Параллельные оценки не приводят к `FeedbackSaveError`.
	- [x] Для одной генерации существует не более одной feedback-записи.
	- [x] Последнее значение оценки соответствует документированной политике.
	- [x] `None` не затирает существующий `feedback`.
	- [x] Тесты проходят на SQLite.
	- [x] PostgreSQL integration-тест подтверждает concurrency behavior.
	- [x] Ошибки БД логируются с `gen_id` и `user_id`.
- [x] AUD-018 — Сделать callback parser строгим
	  - Описание: parser принимает malformed callback-data, например `fb:like:abc`, и возвращает callback с `gen_id=None`.
	  - ТЗ:
	- Определить точный формат callback: `fb:{action}:{positive_gen_id}`.
	- Отклонять отсутствующий `gen_id`, нечисловой `gen_id`, `gen_id <= 0` и лишние сегменты.
	- Убрать неявный fallback на FSM для malformed callback.
	- Заменить `callback_gen_id or flow_state.gen_id` на явную проверку `is not None`.
	- Обновить evaluation и feedback handlers.
	  - DoD:
	- [x] `fb:like:123` успешно парсится.
	- [x] `fb:dislike:123` успешно парсится.
	- [x] `fb:like`, `fb:like:abc`, `fb:like:0`, `fb:like:-1` отклоняются.
	- [x] `fb:like:1:extra` отклоняется.
	- [x] Malformed callback не вызывает `save_feedback`.
	- [x] Есть unit-тесты parser и handler-тесты.
	- [x] `pytest` и Ruff проходят.
- [x] AUD-025 — Синхронизировать feedback contract в документации
	  - Описание: `ARCHITECTURE.md` использует устаревшие поля `is_positive` и `comment`, тогда как ORM-модель использует `is_liked` и `feedback`.
	  - ТЗ:
	- Найти все упоминания `is_positive`, `comment` и старых имён feedback-полей.
	- Сверить документацию с `domains.feedback.models.GenerationFeedback`.
	- Обновить таблицы хранения данных, диаграммы и описания API.
	- Добавить поиск устаревших имён в CI или отдельную documentation-проверку.
	  - DoD:
	- [x] В документации используются только актуальные имена `is_liked` и `feedback`.
	- [x] Поиск по проекту не находит подтверждённых старых имён.
	- [x] Ссылки на feedback service и handlers работают.
	- [x] CI проходит.
- [x] AUD-025 — Синхронизировать feedback contract в документации
- [x] AUD-001 — Сделать feedback upsert совместимым с SQLite и PostgreSQL
	  - Примечание: базовая совместимость исправлена, но конкурентный сценарий вынесен в новый AUD-019.
- [x] AUD-002 — Определить единственный `task_registry.py`
- [x] AUD-003 — Удалить или подключить `task_id` к реальному flow
- [x] AUD-004 — Сделать startup cleanup Redis безопасным
	  - Примечание: базовая проверка других экземпляров добавлена, но ownership cleanup требует дополнительного аудита AUD-023.
- [x] AUD-006 — Убрать compatibility FSM-реэкспорт
- [x] AUD-007 — Синхронизировать тесты отмены с registry-контрактом
- [x] AUD-008 — Устранить дублирующий `COPY workers` в Dockerfile
- [x] AUD-009 — Синхронизировать `ARCHITECTURE.md` с кодом
	  - Примечание: основные имена синхронизированы, но обнаружен новый drift feedback contract — AUD-025.
- [x] AUD-010 — Формализовать единый cancellation flow
	  - Примечание: базовая state machine и четыре этапа отмены описаны, но delivery cancellation требует отдельного delivery state — AUD-022.
- [x] AUD-011 — Определить политику ошибки сохранения истории промпта
- [x] AUD-012 — Исправить docstring `_save_feedback_best_effort`
- [x] AUD-013 — Ввести единый typed parser callback-data
	  - Примечание: typed parser появился, но validation недостаточно строгая — AUD-018.
- [x] AUD-014 — Устранить дублирование `task_id` и `attempt_id`
- [x] AUD-015 — Добавить настоящий Redis integration-профиль
	  - Примечание: добавлен fakeredis integration-профиль; тест настоящего Redis вынесен в AUD-027.
- [ ] AUD-016 — Зафиксировать границы framework-зависимости портов
- [x] AUD-017 — Финальная зачистка legacy-импортов и комментариев
	  - Примечание: основной cleanup выполнен, новые stale claims и documentation drift вынесены в AUD-025 и AUD-032.
- [x] AUD-030 — Проверить права восстановленного audio volume
	  - Описание: `entrypoint.sh` меняет владельца `/data` без рекурсивной проверки существующих файлов.
	  - ТЗ:
	- Проверить сценарий запуска с непустым volume.
	- Проверить владельца и права существующих MP3.
	- Решить, нужен ли рекурсивный `chown` или отдельная init-процедура.
	- Добавить deployment-тест или документировать требуемые права volume.
	  - DoD:
	- [x] Botuser может читать существующие MP3 после рестарта.
	- [x] Botuser может создавать новые MP3.
	- [x] Сценарий непустого volume проверяется автоматически или документирован.
	- [x] В контейнер не добавлены лишние права.




%% kanban:settings
```
{"kanban-plugin":"board","list-collapse":[true,false,false,false,false,false,false,false],"show-checkboxes":false,"move-tags":true,"tag-action":"obsidian"}
```
%%
