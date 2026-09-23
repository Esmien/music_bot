1. handle_evaluate - Отрабатывает на состояние waiting_evaluation, рисует кнопки 👍/👎 из
	keyboards. evaluation_keyboards.get_evaluation_keyboard
	Проверяет, какая это оценка (лайк/дизлайк) и записывает в FSM в формате bool (True - это лайк)
	Переводит стейт в waiting_feedback
2. handle_feedback - ловит waiting_feedback, рисует кнопки keyboards.evaluation_keyboards.get_feedback_keyboard, ждет сообщения пользователя.
	При нажатии на любую кнопку пишет "✅ Спасибо, ваша оценка принята!" и отправляет id пользователя, фидбек (если он меньше settings.MIN_FEEDBACK_TEXT, то None) и оценку из FSM в services.evaluation.save_feedback