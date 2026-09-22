from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import UIConfig


FEEDBACK_CB_PREFIX = "fb:"

CB_FEEDBACK_LIKE = f"{FEEDBACK_CB_PREFIX}like"
CB_FEEDBACK_DISLIKE = f"{FEEDBACK_CB_PREFIX}dislike"
CB_FEEDBACK_SEND = f"{FEEDBACK_CB_PREFIX}send"
CB_FEEDBACK_FINISH = f"{FEEDBACK_CB_PREFIX}finish"


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