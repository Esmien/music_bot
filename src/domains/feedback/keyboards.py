from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from domains.feedback.feedback_messages import FEEDBACK_FINISH_BUTTON, FEEDBACK_SEND_BUTTON

CB_FEEDBACK_SEND = "fb:send"
CB_FEEDBACK_FINISH = "fb:finish"


def get_feedback_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру выбора между отправкой отзыва и завершением.

    Returns:
        Инлайн-клавиатура с кнопками отправки фидбека и завершения без отзыва.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=FEEDBACK_SEND_BUTTON, callback_data=CB_FEEDBACK_SEND)
    builder.button(text=FEEDBACK_FINISH_BUTTON, callback_data=CB_FEEDBACK_FINISH)
    builder.adjust(1)
    return builder.as_markup()


def get_feedback_finish_keyboard() -> InlineKeyboardMarkup:
    """Собирает клавиатуру ожидания отзыва с одной кнопкой завершения.

    Returns:
        Инлайн-клавиатура с кнопкой завершения сценария без отзыва.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=FEEDBACK_FINISH_BUTTON, callback_data=CB_FEEDBACK_FINISH)
    builder.adjust(1)
    return builder.as_markup()
