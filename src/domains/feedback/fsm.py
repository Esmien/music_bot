"""FSM-состояния сценария оценки и сбора фидбека."""

from aiogram.fsm.state import State, StatesGroup


class FeedbackStates(StatesGroup):
    """FSM-состояния сценария сбора фидбека по генерации.

    Attributes:
        waiting_evaluation: Ожидание оценки (понравилось/не понравилось).
        waiting_for_feedback_choice: Ожидание выбора: отправить отзыв или завершить.
        waiting_feedback: Ожидание фидбека (сообщение пользователя, что хорошо, а что нет).
    """

    waiting_evaluation = State()
    waiting_for_feedback_choice = State()
    waiting_feedback = State()
