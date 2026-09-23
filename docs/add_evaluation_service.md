1. Добавить services/evaluation.py
2. Функции:
		save_feedback(user_id: int, feedback: str, evalue: bool) -> None: ... # Принимает фидбек пользователя и пишет в базу при условии:
		if feedback or evalue.
		Запись в базу идет и оценки, и фидбека. Если негативная оценка без фидбека - не пишем