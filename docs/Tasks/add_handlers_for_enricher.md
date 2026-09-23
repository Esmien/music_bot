Флоу:
1. Юзер в стейте `waiting_for_idea` присылает текст.
    
2. Бот делает `await state.update_data(prompt=text)`, отправляет текст в Enricher (Open WebUI), дергает enrich_prompt(prompt) и показывает лоадер.
    
3. Приходит обогащенный промпт  (сохраняем в result) -> `await state.update_data(enriched_prompt=result)`.
    
4.  Бот выдает текст с инлайн-кнопками (Одобрить / Изменить) и ставит стейт `waiting_for_approval`.
    
5. Если нажато «Изменить» -> переводим в `waiting_for_edits`, ждем текст с правками, заново стучимся в enricher.enrich_prompt() с обогащенным промптом, возвращаемся на шаг 3.
    
6. Если «Одобрить» -> стейт `waiting_for_title` (переиспользуем существующий, позже доработаем для разделения чистого и обогащенного), ждем название -> финализируем и кидаем в генератор аудио.

Оффтоп: после генерации хэндлер handlers.generation.handle_title переводит стейт в waiting_evaluation

Пример уходящего сообщения в энричер после правок пользователя. С этим склеивается системный промпт из Open WebUI
messages = [
    {"role": "user", "content": data["prompt"]},
    {"role": "assistant", "content": data["enriched_prompt"]},
    {"role": "user", "content": edits_text},
]