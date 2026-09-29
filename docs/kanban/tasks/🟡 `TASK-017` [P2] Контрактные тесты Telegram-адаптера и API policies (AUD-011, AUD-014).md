- [ ] **Приоритет:** P2 (MEDIUM)
	**Метки:** `reliability`, `observability`, `telegram`
	**Проблема:** Недостаточное покрытие контрактов порта `TelegramPort` и его реализаций (`AiogramTelegramPort`, `FakeTelegramPort`).
	**Решение:**
	  1. Разработать набор контрактных тестов, проверяющих одинаковое поведение `AiogramTelegramPort` и `FakeTelegramPort`.
	  2. Протестировать обработку граничных ситуаций при отправке больших сообщений и файлов.
	**DoD:**
	  - [ ] Контрактные тесты покрывают методы `send_message`, `send_audio`, `edit_message`, `notify_owner`.
	  - [ ] Проверено корректное поведение при отсутствии порта в зависимостях.
	  - [ ] Ruff, Mypy, Pytest проходят без ошибок.
