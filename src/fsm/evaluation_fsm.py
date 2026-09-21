"""FSM-состояния сценария обогащения промпта и сбора фидбека.

Реестр ожидающих ключ pending_auth вынесен в fsm/auth_registry.py,
чистка осиротевших флагов генерации — в fsm/generation_flags.py.
"""

from aiogram.fsm.state import State, StatesGroup


class PromptEnricherStates(StatesGroup):
    """FSM-состояния сценария обогащения промпта.

    Attributes:
        waiting_for_idea: Ожидание промпта пользователя.
        waiting_for_approval: Ожидание подтверждения сгенерированного промпта.
        waiting_for_edits: Ожидание правок сгенерированного промпта.
    """

    waiting_for_idea = State()
    waiting_for_approval = State()
    waiting_for_edits = State()


class FeedbackStates(StatesGroup):
    """FSM-состояния сценария сбора фидбека по генерации.

    Attributes:
        waiting_evaluation: Ожидание оценки (понравилось/не понравилось).
        waiting_feedback: Ожидание фидека (сообщение пользователя, что ок, что нет).
    """

    waiting_evaluation = State()
    waiting_feedback = State()
