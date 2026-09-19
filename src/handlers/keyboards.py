"""Клавиатуры бота."""

from aiogram.utils.keyboard import ReplyKeyboardBuilder


def get_main_keyboard():
    """Основная reply-клавиатура: генерация, кредиты, выход.

    Returns:
        ReplyKeyboardMarkup с двумя кнопками в первом ряду
        и "🚪 Выйти" во втором.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎵 Сгенерировать")
    builder.button(text="💳 Кредиты")
    builder.button(text="🚪 Выйти")
    # adjust(2): первые две кнопки в один ряд, "Выйти" — на отдельный,
    # чтобы её случайно не нажать рядом с генерацией
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard():
    """Клавиатура с единственной кнопкой отмены текущей операции.

    Returns:
        ReplyKeyboardMarkup с кнопкой "❌ Отмена".
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    return builder.as_markup(resize_keyboard=True)
