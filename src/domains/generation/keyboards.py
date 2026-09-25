"""Клавиатуры сценария генерации."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_retry_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для повтора неудачной генерации.

    Returns:
        Inline-клавиатура с кнопкой повторного запуска.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="retry_generation")]
        ]
    )
