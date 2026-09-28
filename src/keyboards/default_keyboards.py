"""Reply-клавиатуры бота.

Единый с enricher_keyboards принцип модуля: идентификаторы кнопок
(здесь — тексты) вынесены в константы уровня модуля, поэтому хендлеры
фильтруют сообщения через импорт (F.text == GENERATE_BUTTON_TEXT),
а не через захардкоженные строки — тексты кнопок и их «ловушки»
в хендлерах не разъезжаются при правках.
"""

from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from core.config import UIConfig


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Основная reply-клавиатура: генерация, кредиты, выход.

    Returns:
        ReplyKeyboardMarkup с двумя кнопками в первом ряду
        и "🚪 Выйти" во втором.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text=UIConfig.GENERATE_BUTTON)
    builder.button(text=UIConfig.CREDITS_BUTTON)
    builder.button(text=UIConfig.LOGOUT_BUTTON)
    # adjust(2): первые две кнопки в один ряд, "Выйти" — на отдельный,
    # чтобы её случайно не нажать рядом с генерацией
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с единственной кнопкой отмены текущей операции.

    Returns:
        ReplyKeyboardMarkup с кнопкой "❌ Отмена".
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text=UIConfig.CANCEL_BUTTON)
    return builder.as_markup(resize_keyboard=True)
