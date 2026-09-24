1. handle_evaluate - Отрабатывает на состояние waiting_evaluation, рисует кнопки 👍/👎 из
	keyboards. evaluation_keyboards.get_evaluation_keyboard
	Проверяет, какая это оценка (лайк/дизлайк) и записывает в FSM в формате bool (True - это лайк)
	Переводит стейт в waiting_feedback
