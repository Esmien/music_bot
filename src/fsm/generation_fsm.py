"""FSM-состояния и лимиты диалога генерации.

Вынесены из хендлеров в отдельный модуль: состояния и лимиты — это
«контракт» диалога, на который опираются и хендлеры, и тесты.
"""

from aiogram.fsm.state import State, StatesGroup

# Полный текст песни (куплеты + припевы) в среднем занимает 1500–3000 символов
MAX_PROMPT_LEN = 4000
MAX_TITLE_LEN = 100


class GenerationStates(StatesGroup):
    """FSM-состояния процесса генерации песни.

    Атрибуты:
        waiting_for_prompt: Ждём описание/текст песни.
        waiting_for_title: Ждём название трека.
    """

    waiting_for_prompt = State()
    waiting_for_title = State()
