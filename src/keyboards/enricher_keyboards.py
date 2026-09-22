"""Инлайн-клавиатуры для сценариев обогащения промпта и фидбека.

callback_data построены по схеме "<домен>:<действие>", чтобы хэндлеры
ловили их через F.data.startswith(...) или точное совпадение.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import UIConfig

# Префиксы callback_data: обогащение промпта и фидбек
PROMPT_CB_PREFIX = "prompt:"

CB_PROMPT_APPROVE = f"{PROMPT_CB_PREFIX}approve"
CB_PROMPT_EDIT = f"{PROMPT_CB_PREFIX}edit"
CB_PROMPT_CANCEL = f"{PROMPT_CB_PREFIX}cancel"


def get_prompt_approval_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру аппрува сгенерированного промпта.

    Returns:
        Инлайн-клавиатура с кнопками подтверждения, правки и отмены.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=UIConfig.PROMPT_APPROVE_BUTTON, callback_data=CB_PROMPT_APPROVE)
    builder.button(text=UIConfig.PROMPT_EDIT_BUTTON, callback_data=CB_PROMPT_EDIT)
    builder.button(text=UIConfig.PROMPT_CANCEL_BUTTON, callback_data=CB_PROMPT_CANCEL)
    builder.adjust(2)
    return builder.as_markup()
