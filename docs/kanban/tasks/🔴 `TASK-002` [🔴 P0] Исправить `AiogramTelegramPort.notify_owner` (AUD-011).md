- [ ] **Приоритет:** 🔴 P0 (BLOCKER)
	**Метки:** `bug`, `blocker`, `telegram`, `reliability`
	**Проблема:** AUD-011. Метод `AiogramTelegramPort.notify_owner()` в `shared/ports/telegram.py` передаёт неподдерживаемые kwargs (`owner_id`, `message`) в `core.utils.error_notify.notify_owner()`, вызывая `TypeError` и потенциальную рекурсию.
	**Решение:**
	  1. Реализовать отправку уведомления в `AiogramTelegramPort.notify_owner` напрямую через `self._bot.send_message(...)` с проверкой настроек `BOT_OWNER_ID`.
	  2. Устранить взаимный рекурсивный вызов между `core.notify_owner` и портом.
	  3. Привести сигнатуры и контракты методов уведомления владельца к единому стандарту.
	**DoD:**
	  - [x] `AiogramTelegramPort.notify_owner()` совместим по вызовам и типам с `core.notify_owner()`.
	  - [x] Рекурсивная связка полностью устранена.
	  - [x] Добавлен регрессионный тест с мокированным Bot при заданном и незаданном `BOT_OWNER_ID`.
	  - [x] Ruff, Mypy, Pytest проходят без ошибок.
