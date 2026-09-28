"""Pydantic-модели FSM-данных для домена обратной связи."""

from pydantic import BaseModel


class FeedbackFlowState(BaseModel):
    """FSM-данные сценария сбора оценки и отзыва.

    Атрибуты:
        feedback_evaluation: Оценка пользователя (True — лайк, False — дизлайк).
        feedback_text: Текстовый отзыв пользователя (опционально).
        feedback_prompt_message_id: ID сообщения с запросом отзыва (для удаления кнопок).
    """

    feedback_evaluation: bool | None = None
    feedback_text: str | None = None
    feedback_prompt_message_id: int | None = None
