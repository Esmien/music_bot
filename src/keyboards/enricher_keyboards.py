"""Инлайн-клавиатуры для сценариев обогащения промпта и фидбека.

callback_data построены по схеме "<домен>:<действие>", чтобы хэндлеры
ловили их через F.data.startswith(...) или точное совпадение.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import UIConfig

# Префиксы callback_data: обогащение промпта и фидбек
PROMPT_CB_PREFIX = "prompt:"
FEEDBACK_CB_PREFIX = "fb:"

CB_PROMPT_APPROVE = f"{PROMPT_CB_PREFIX}approve"
CB_PROMPT_EDIT = f"{PROMPT_CB_PREFIX}edit"
CB_PROMPT_CANCEL = f"{PROMPT_CB_PREFIX}cancel"
CB_FEEDBACK_LIKE = f"{FEEDBACK_CB_PREFIX}like"
CB_FEEDBACK_DISLIKE = f"{FEEDBACK_CB_PREFIX}dislike"
CB_FEEDBACK_SEND = f"{FEEDBACK_CB_PREFIX}send"
CB_FEEDBACK_FINISH = f"{FEEDBACK_CB_PREFIX}finish"


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


def get_evaluation_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру оценки результата генерации.

    Returns:
        Инлайн-клавиатура с кнопками «нравится» и «не нравится».
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=UIConfig.EVALUATION_LIKE_BUTTON, callback_data=CB_FEEDBACK_LIKE)
    builder.button(text=UIConfig.EVALUATION_DISLIKE_BUTTON, callback_data=CB_FEEDBACK_DISLIKE)
    builder.adjust(2)
    return builder.as_markup()


def get_feedback_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру завершения сценария фидбека.

    Returns:
        Инлайн-клавиатура с кнопками отправки фидбека и завершения.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=UIConfig.FEEDBACK_SEND_BUTTON, callback_data=CB_FEEDBACK_SEND)
    builder.button(text=UIConfig.FEEDBACK_FINISH_BUTTON, callback_data=CB_FEEDBACK_FINISH)
    builder.adjust(1)
    return builder.as_markup()
