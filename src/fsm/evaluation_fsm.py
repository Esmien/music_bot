"""FSM-состояния сценария обогащения промпта и сбора фидбека.

Реестр авторизации перенесён в domains/auth/service.py,
чистка осиротевших флагов генерации — в fsm/generation_flags.py.
"""

from aiogram.fsm.state import State, StatesGroup


class FeedbackStates(StatesGroup):
    """FSM-состояния сценария сбора фидбека по генерации.

    Attributes:
        waiting_evaluation: Ожидание оценки (понравилось/не понравилось).
        waiting_for_feedback_choice: Ожидание выбора: отправить отзыв или завершить.
        waiting_feedback: Ожидание фидека (сообщение пользователя, что ок, что нет).
    """

    waiting_evaluation = State()
    waiting_for_feedback_choice = State()
    waiting_feedback = State()
