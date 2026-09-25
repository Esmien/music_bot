from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import UIConfig

CB_FEEDBACK_LIKE = "fb:like"
CB_FEEDBACK_DISLIKE = "fb:dislike"


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
