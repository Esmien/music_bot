"""FSM-состояния сценария обогащения промпта."""

from aiogram.fsm.state import State, StatesGroup


class PromptEnricherStates(StatesGroup):
    """Состояния диалога обогащения промпта.

    Attributes:
        waiting_for_idea: Ожидание идеи песни от пользователя.
        waiting_for_approval: Ожидание подтверждения результата.
        waiting_for_edits: Ожидание правок к результату.
    """

    waiting_for_idea = State()
    waiting_for_approval = State()
    waiting_for_edits = State()
