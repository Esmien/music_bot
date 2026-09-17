"""Клавиатуры бота."""

from aiogram.utils.keyboard import ReplyKeyboardBuilder


def get_main_keyboard():
    """Основная клавиатура: генерация, кредиты, выход."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎵 Сгенерировать")
    builder.button(text="💳 Кредиты")
    builder.button(text="🚪 Выйти")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard():
    """Клавиатура с кнопкой отмены текущей операции."""
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    return builder.as_markup(resize_keyboard=True)
