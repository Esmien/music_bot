"""Reply-клавиатуры домена base."""

from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

GENERATE_BUTTON = "🎵 Сгенерировать"
CREDITS_BUTTON = "💳 Кредиты"
LOGOUT_BUTTON = "🚪 Выйти"
CANCEL_BUTTON = "❌ Отмена"


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Создаёт основную клавиатуру: генерация, кредиты и выход.

    Returns:
        ReplyKeyboardMarkup с двумя кнопками в первом ряду
        и кнопкой выхода во втором.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text=GENERATE_BUTTON)
    builder.button(text=CREDITS_BUTTON)
    builder.button(text=LOGOUT_BUTTON)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Создаёт клавиатуру с единственной кнопкой отмены.

    Returns:
        ReplyKeyboardMarkup с кнопкой отмены.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text=CANCEL_BUTTON)
    return builder.as_markup(resize_keyboard=True)
