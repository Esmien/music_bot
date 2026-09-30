from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from domains.feedback.feedback_messages import FEEDBACK_FINISH_BUTTON, FEEDBACK_SEND_BUTTON

CB_FEEDBACK_SEND = "fb:send"
CB_FEEDBACK_FINISH = "fb:finish"


def get_feedback_keyboard(gen_id: int | None = None) -> InlineKeyboardMarkup:
    """Собирает клавиатуру выбора между отправкой отзыва и завершением.

    Args:
        gen_id: Опциональный ID генерации для привязки к кнопкам.

    Returns:
        Инлайн-клавиатура с кнопками отправки фидбека и завершения без отзыва.
    """
    builder = InlineKeyboardBuilder()
    send_data = f"{CB_FEEDBACK_SEND}:{gen_id}" if gen_id is not None else CB_FEEDBACK_SEND
    finish_data = f"{CB_FEEDBACK_FINISH}:{gen_id}" if gen_id is not None else CB_FEEDBACK_FINISH
    builder.button(text=FEEDBACK_SEND_BUTTON, callback_data=send_data)
    builder.button(text=FEEDBACK_FINISH_BUTTON, callback_data=finish_data)
    builder.adjust(1)
    return builder.as_markup()


def get_feedback_finish_keyboard(gen_id: int | None = None) -> InlineKeyboardMarkup:
    """Собирает клавиатуру ожидания отзыва с одной кнопкой завершения.

    Args:
        gen_id: Опциональный ID генерации для привязки к кнопке.

    Returns:
        Инлайн-клавиатура с кнопкой завершения сценария без отзыва.
    """
    builder = InlineKeyboardBuilder()
    finish_data = f"{CB_FEEDBACK_FINISH}:{gen_id}" if gen_id is not None else CB_FEEDBACK_FINISH
    builder.button(text=FEEDBACK_FINISH_BUTTON, callback_data=finish_data)
    builder.adjust(1)
    return builder.as_markup()
