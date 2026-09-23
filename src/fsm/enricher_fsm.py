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
