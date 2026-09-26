from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CB_FEEDBACK_LIKE = "fb:like"
CB_FEEDBACK_DISLIKE = "fb:dislike"

EVALUATION_LIKE_BUTTON = "👍"
EVALUATION_DISLIKE_BUTTON = "👎"


def get_evaluation_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру оценки результата генерации.

    Returns:
        Инлайн-клавиатура с кнопками «нравится» и «не нравится».
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=EVALUATION_LIKE_BUTTON, callback_data=CB_FEEDBACK_LIKE)
    builder.button(text=EVALUATION_DISLIKE_BUTTON, callback_data=CB_FEEDBACK_DISLIKE)
    builder.adjust(2)
    return builder.as_markup()
