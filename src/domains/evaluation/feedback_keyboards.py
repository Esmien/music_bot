from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import UIConfig
from domains.evaluation.keyboards import CB_FEEDBACK_FINISH, CB_FEEDBACK_SEND


def get_feedback_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру выбора между отправкой отзыва и завершением.

    Returns:
        Инлайн-клавиатура с кнопками отправки фидбека и завершения без отзыва.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=UIConfig.FEEDBACK_SEND_BUTTON, callback_data=CB_FEEDBACK_SEND)
    builder.button(text=UIConfig.FEEDBACK_FINISH_BUTTON, callback_data=CB_FEEDBACK_FINISH)
    builder.adjust(1)
    return builder.as_markup()


def get_feedback_finish_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру ожидания отзыва с одной кнопкой завершения.

    Returns:
        Инлайн-клавиатура с кнопкой завершения сценария без отзыва.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=UIConfig.FEEDBACK_FINISH_BUTTON, callback_data=CB_FEEDBACK_FINISH)
    builder.adjust(1)
    return builder.as_markup()
