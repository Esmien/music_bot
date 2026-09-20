### FSM PromptEnricherStates:
1. waiting_for_idea - ожидание промпта пользователя
2. waiting_for_approval - ожидание подтверждения сгенерированного промпта
3. waiting_for_edits - ожидание правок сгенерированного промпта (если не понравился изначальный вариант)

### FeedbackStates
1. waiting_evaluation - ожидание оценки (понравилось/не понравилось)
2. waiting_feedback - ожидание фидбека (сообщение пользователя, что ок, что нет)