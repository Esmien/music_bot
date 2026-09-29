- [ ] **Приоритет:** 🔴 P0 (BLOCKER)
	**Метки:** `bug`, `reliability`, `tech-debt`, `docs`
	**Проблема:** AUD-001. `docker-compose.yml` запускает воркеры `evaluation`, `credits`, `feedback`, тогда как `workers/taskiq_worker.py` поддерживает только `enricher` и `generation`, что приводит к crash-loop в контейнерах.
	**Решение:**
	  1. Зафиксировать допустимые контуры воркеров: `enricher-worker` и `generation-worker`.
	  2. Удалить неиспользуемые сервисы воркеров из `infra/docker-compose.yml`.
	  3. Удалить неиспользуемые брокеры (`evaluation_broker`, `credits_broker`, `feedback_broker`) из `core/broker.py` и точек импорта.
	  4. Сохранить доменные Telegram handlers и сервисы без TaskIQ-обвязки там, где логика выполняется синхронно.
	  5. Актуализировать документацию и переменные окружения.
	**DoD:**
	  - [ ] В `docker-compose.yml` оставлены только сервисы `enricher-worker` и `generation-worker`.
	  - [ ] Отсутствуют неиспользуемые экземпляры брокеров и лишние подключения к очередям RabbitMQ.
	  - [ ] Модули доменов `evaluation`, `credits`, `feedback` сохраняют работоспособность и корректно импортируются.
	  - [ ] Проходят тесты для хендлеров credits, evaluation и feedback.
	  - [ ] Команда `docker compose config` отрабатывает без ошибок с кодом 0.
	  - [ ] Обновлены `ARCHITECTURE.md` и связанные описания.
	  - [ ] Ruff, Mypy, Pytest проходят без ошибок.