from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from shared.callback_parser import FeedbackAction

CB_FEEDBACK_LIKE = f"fb:{FeedbackAction.LIKE}"
CB_FEEDBACK_DISLIKE = f"fb:{FeedbackAction.DISLIKE}"

EVALUATION_LIKE_BUTTON = "👍"
EVALUATION_DISLIKE_BUTTON = "👎"


def get_evaluation_keyboard(gen_id: int | None = None) -> InlineKeyboardMarkup:
    """Собирает клавиатуру оценки результата генерации.

    Args:
        gen_id: Опциональный ID генерации для привязки к кнопкам.

    Returns:
        Инлайн-клавиатура с кнопками «нравится» и «не нравится».
    """
    builder = InlineKeyboardBuilder()
    like_data = f"{CB_FEEDBACK_LIKE}:{gen_id}" if gen_id is not None else CB_FEEDBACK_LIKE
    dislike_data = f"{CB_FEEDBACK_DISLIKE}:{gen_id}" if gen_id is not None else CB_FEEDBACK_DISLIKE
    builder.button(text=EVALUATION_LIKE_BUTTON, callback_data=like_data)
    builder.button(text=EVALUATION_DISLIKE_BUTTON, callback_data=dislike_data)
    builder.adjust(2)
    return builder.as_markup()
