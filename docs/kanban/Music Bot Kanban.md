---

kanban-plugin: board

---

## Quick wins



## 🔴 P0 — BLOCKER



## 🟠 P1 — HIGH



## 🟡 P2 — MEDIUM



## 🟢 P3 — LOW



## In progress

- [ ] AUD-017 — Финальная зачистка legacy-импортов и комментариев
	  - Описание: после основных исправлений нужно удалить подтверждённые compatibility-следы, устаревшие `noqa` и комментарии.
	  - ТЗ: запустить Ruff и поиск legacy/TODO; удалить только подтверждённый мёртвый код; обновить документацию.
	  - DoD:
	- [x] Нет подтверждённых неиспользуемых импортов.
	- [x] Нет устаревших compatibility-комментариев.
	- [ ] CI проходит.


## Done

- [ ] AUD-016 — Зафиксировать границы framework-зависимости портов
	  - Описание: порты принимают конкретные `aiogram.Message` и `FSMContext`.
	  - ТЗ: решить, является ли это осознанной pragmatic-архитектурой; документировать границу либо ввести DTO; добавить архитектурную проверку.
	  - DoD:
	- [ ] Документировано, где допустим импорт aiogram.
	- [ ] Междоменные зависимости идут через единый контракт.
- [x] AUD-015 — Добавить настоящий Redis integration-профиль
	  - Описание: Lua `EVAL` частично эмулируется monkeypatch.
	  - ТЗ: оставить unit-тесты; добавить Redis integration profile и CI-команду; проверить `SET NX PX` и compare-and-delete.
	  - DoD:
	- [x] Реальный Redis подтверждает mutual exclusion.
	- [x] Чужой token не удаляет lock.
	- [x] Expired lock захватывается повторно.
	- [x] CI явно требует Redis.
- [x] AUD-014 — Устранить дублирование `task_id` и `attempt_id`
	  - Описание: конкурентная защита реализована через `attempt_id`, а `task_id` остаётся незавершённой концепцией.
	  - ТЗ: описать назначение, владельца и TTL каждого ID; удалить неиспользуемый либо связать со state machine; обновить тесты.
	  - DoD:
	- [x] Для каждого ID есть проверяемый use case.
	- [x] Нет двух недокументированных механизмов одного инварианта.
	- [x] Concurrent worker test проходит.
- [x] AUD-009 — Синхронизировать `ARCHITECTURE.md` с кодом
	  - Описание: документация содержит устаревшие имена FSM и handlers.
	  - ТЗ: сверить диаграммы с исходниками; обновить имена, пути и cancellation flow; проверить ссылки.
	  - DoD:
	- [x] Имена и пути из диаграмм существуют или помечены концептуальными.
	- [x] Feedback и cancellation flow актуальны.
- [x] AUD-003 — Удалить или подключить `task_id` к реальному flow
	  - Описание: Redis хранит `task_id`, но worker не использует его для проверки актуальности результата.
	  - ТЗ: проверить вызовы `get_task_id`; удалить API либо встроить проверку перед результатом; обновить тесты и документацию.
	  - DoD:
	- [x] `task_id` участвует в инварианте либо полностью удалён.
	- [x] Нет тестов несуществующей функциональности.
- [ ] AUD-011 — Определить политику ошибки сохранения истории промпта
	  - Описание: best-effort сохранение может потерять запись и продолжить flow.
	  - ТЗ: решить blocking или best-effort; для best-effort добавить метрику/алерт; для blocking не запускать генерацию после ошибки; добавить тесты.
	  - DoD:
	- [ ] Политика описана в документации.
	- [ ] Flow соответствует выбранной политике.
	- [ ] Ошибка логируется с user/generation context.
	- [ ] `SQLAlchemyError` покрыт тестом.
- [x] AUD-011 — Определить политику ошибки сохранения истории промпта
	  - Описание: best-effort сохранение может потерять запись и продолжить flow.
	  - ТЗ: решить blocking или best-effort; для best-effort добавить метрику/алерт; для blocking не запускать генерацию после ошибки; добавить тесты.
	  - DoD:
	- [x] Политика описана в документации.
	- [x] Flow соответствует выбранной политике.
	- [x] Ошибка логируется с user/generation context.
	- [x] `SQLAlchemyError` покрыт тестом.
- [x] AUD-010 — Формализовать единый cancellation flow
	  - Описание: отмена распределена между `asyncio.Task`, Redis, FSM и БД.
	  - ТЗ: описать state machine и source of truth; проверить отмену до claim, во время API, после сохранения MP3 и во время Telegram delivery; добавить конкурентные тесты.
	  - DoD:
	- [x] Допустимые переходы статусов задокументированы.
	- [x] Stale `attempt_id` не меняет статус и не отправляет результат.
	- [x] Cancel-token очищается или истекает по задокументированному TTL.
	- [x] Есть тесты четырёх этапов отмены.
- [x] AUD-007 — Синхронизировать тесты отмены с registry-контрактом
	  - Описание: часть тестов обращается к Redis registry как к словарю.
	  - ТЗ: найти старые фикстуры; разделить local `asyncio` cancellation и Redis cancel-token; проверить очистку registry и FSM.
	  - DoD:
	- [x] В тестах нет несуществующего dictionary API.
	- [x] Локальная отмена и Redis cancel-token проверяются отдельно.
	- [x] После отмены очищаются registry и FSM.
- [x] AUD-004 — Сделать startup cleanup Redis безопасным
	  - Описание: общая очистка active-task и FSM-ключей опасна при rolling restart.
	  - ТЗ: выбрать ownership/lease либо явно закрепить single-instance; добавить тест перекрывающегося запуска; логировать обработанные и пропущенные ключи.
	  - DoD:
	- [x] Один экземпляр не удаляет состояние другого.
	- [x] Deployment constraint документирован или есть ownership-тест.
	- [x] `RedisError` покрыт тестом.
- [x] AUD-002 — Определить единственный `task_registry.py`
	  - Описание: обнаружены in-memory и Redis-версии реестра.
	  - ТЗ: найти физические пути и импорты; зафиксировать canonical вариант; удалить или переименовать legacy; привести тесты к контракту.
	  - DoD:
	- [x] Один production registry API.
	- [x] Все импорты используют canonical API.
	- [x] Тесты регистрации, отмены и очистки проходят.
- [x] AUD-013 — Ввести единый typed parser callback-data
	  - Описание: feedback и evaluation handlers по-разному разбирают `fb:action:gen_id`.
	  - ТЗ: создать enum action и typed parser; перевести оба обработчика; добавить valid, malformed и stale callback-тесты.
	  - DoD:
	- [x] В handlers нет ручного `split` для этого формата.
	- [x] Malformed callback безопасно отклоняется.
	- [x] Stale `gen_id` не записывается в БД.
- [x] AUD-012 — Исправить docstring `_save_feedback_best_effort`
	  - Описание: docstring содержит отсутствующий параметр `chat_id`.
	  - ТЗ: синхронизировать `Args` с сигнатурой и проверить остальные описания.
	  - DoD:
	- [x] Все параметры сигнатуры описаны.
	- [x] Лишних параметров нет.
	- [x] Ruff проходит.
- [x] AUD-008 — Устранить дублирующий `COPY workers` в Dockerfile
	  - Описание: после `COPY src/ ./` каталог `workers` копируется повторно.
	  - ТЗ: проверить структуру `src/workers`; оставить один способ копирования; собрать bot и worker-образы.
	  - DoD:
	- [x] Дублирующего `COPY` нет.
	- [x] Bot-образ собирается.
	- [x] Generation и enricher worker запускаются.
- [x] AUD-006 — Убрать compatibility FSM-реэкспорт
	  - Описание: legacy `fsm.py` создаёт альтернативную точку импорта `FeedbackStates`.
	  - ТЗ: найти импорты; перевести их на `domains.feedback.fsm`; удалить реэкспорт или пометить deprecated; обновить тесты.
	  - DoD:
	- [x] Используется один canonical import.
	- [x] `pytest` проходит.
	- [x] Ruff проходит.
- [x] AUD-001 — Сделать feedback upsert совместимым с SQLite и PostgreSQL
	  - Описание: сервис использует PostgreSQL-specific `insert/on_conflict_do_update`, а тесты используют SQLite.
	  - ТЗ: выбрать dialect-independent стратегию или реализации по dialect; сохранить ownership, статус `SUCCESS` и отсутствие дублей; добавить SQLite и PostgreSQL проверки.
	  - DoD:
	- [x] Feedback-тесты проходят на SQLite.
	- [x] PostgreSQL проверяет создание и обновление записи.
	- [x] На `generation_id` не более одной записи.
	- [x] `None` не затирает существующие поля.
	- [x] Ошибка БД преобразуется в `FeedbackSaveError`.




%% kanban:settings
```
{"kanban-plugin":"board","list-collapse":[true,true,true,true,false,false,false,false],"show-checkboxes":false,"move-tags":true,"tag-action":"obsidian"}
```
%%
