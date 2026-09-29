- [x] **Приоритет:** P1 (HIGH)
	**Метки:** `architecture`, `reliability`, `tech-debt`
	**Проблема:** AUD-017. Воркер генерации напрямую взаимодействует с Telegram UI, обновляет FSM оценки и шлет сообщения вместо публикации доменного события `GenerationSucceeded`.
	**Решение:**
	  1. Вернуть публикацию события `GenerationSucceeded` по окончании генерации.
	  2. Убрать из воркера генерации зависимости от FSM контекста оценки (`FeedbackStates`, `get_evaluation_keyboard`, `EVALUATION_PROMPT_TEXT`).
	  3. Создать в домене оценки обработчик события `GenerationSucceeded`, который проверяет `gen_id`, переводит FSM в `waiting_evaluation` и отправляет клавиатуру пользователю.
	**DoD:**
	  - [x] `domains/generation/worker.py` не содержит импортов и логики FSM оценки (`FeedbackStates`, `StorageKey`, `get_evaluation_keyboard`).
	  - [x] Успешная генерация публикует событие `GenerationSucceeded`.
	  - [x] Сбой генерации публикует `GenerationFailed(stage="generation")`.
	  - [x] Обработчик события реализован в домене оценки с использованием `TelegramPort` и `context.state`.
	  - [x] Добавлен интеграционный тест сквозного флоу.
	  - [x] `ARCHITECTURE.md` приведен в соответствие с фактической схемой.
	  - [x] Ruff, Mypy, Pytest проходят без ошибок.
