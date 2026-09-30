- [ ] **Приоритет:** 🔴 P0 (BLOCKER)
	**Метки:** `bug`, `blocker`, `taskiq`, `reliability`, `testing`, `architecture`
	**Проблема:** AUD-018. `context["telegram_port"]` в `domains/enricher/handlers.py` вызывает `TypeError` в production, так как `taskiq.Context` не поддерживает обращение по индексу. Юнит-тесты маскируют проблему использованием словаря `TaskiqState`.
	**Решение:**
	  1. Заменить обращение `context["key"]` на `context.state["key"]` во всех обработчиках событий TaskIQ.
	  2. Провести глобальный поиск по проекту паттернов `context[`, `context.get(`, `TaskiqDepends`.
	  3. Обновить тестовые фикстуры, чтобы они передавали валидный контекст с атрибутом `.state`.
	  4. Добавить integration-тест на запуск через TaskIQ execution path.
	**DoD:**
	  - [x] В event handlers нет `context[...]`.
	  - [x] Все зависимости читаются через `context.state` или вспомогательный хелпер.
	  - [x] Проведён аудит всех вхождений `context[...]` по кодовой базе.
	  - [x] Тесты не используют `TaskiqState` как замену production-контекста без `.state`.
	  - [x] Добавлен integration test через реальный путь исполнения TaskIQ.
	  - [x] Ruff, Mypy, Pytest проходят без ошибок.