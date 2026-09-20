1. Добавить модель GenerationFeedback с полями:
	1. id (int)
	2. user_id (int), relationship User.user_id
	3. initial_prompt (Text), # промпт от пользователя
	4. enriched_prompt (Text) # обработанный ИИ промпт
	5. is_liked (bool) # понравилось/не понравилось
	6. feedback (Text | None) # опциональное поле, пользователь пишет свое короткое резюме о сгенерированной песне