"""Reply-клавиатуры домена base."""

from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from core.config import UIConfig


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Создаёт основную клавиатуру: генерация, кредиты и выход.

    Returns:
        ReplyKeyboardMarkup с двумя кнопками в первом ряду
        и кнопкой выхода во втором.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text=UIConfig.GENERATE_BUTTON)
    builder.button(text=UIConfig.CREDITS_BUTTON)
    builder.button(text=UIConfig.LOGOUT_BUTTON)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Создаёт клавиатуру с единственной кнопкой отмены.

    Returns:
        ReplyKeyboardMarkup с кнопкой отмены.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text=UIConfig.CANCEL_BUTTON)
    return builder.as_markup(resize_keyboard=True)
