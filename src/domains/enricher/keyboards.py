"""Инлайн-клавиатуры сценария обогащения промпта."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

PROMPT_CB_PREFIX = "prompt:"

CB_PROMPT_APPROVE = f"{PROMPT_CB_PREFIX}approve"
CB_PROMPT_EDIT = f"{PROMPT_CB_PREFIX}edit"
CB_PROMPT_CANCEL = f"{PROMPT_CB_PREFIX}cancel"
CB_PROMPT_RETRY = f"{PROMPT_CB_PREFIX}retry"
CB_PROMPT_FALLBACK = f"{PROMPT_CB_PREFIX}fallback"
CB_TITLE_LEAVE_AS_IS = f"{PROMPT_CB_PREFIX}title_leave"

PROMPT_APPROVE_BUTTON = "✅ Подтвердить"
PROMPT_EDIT_BUTTON = "✏️ Изменить"
PROMPT_CANCEL_BUTTON = "❌ Отменить"
PROMPT_RETRY_BUTTON = "🔄 Попробовать снова"
PROMPT_FALLBACK_BUTTON = "⏭ Без обогащения"


def get_prompt_approval_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру подтверждения результата.

    Returns:
        Клавиатура подтверждения, правки или отмены.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=PROMPT_APPROVE_BUTTON, callback_data=CB_PROMPT_APPROVE)
    builder.button(text=PROMPT_EDIT_BUTTON, callback_data=CB_PROMPT_EDIT)
    builder.button(text=PROMPT_CANCEL_BUTTON, callback_data=CB_PROMPT_CANCEL)
    builder.adjust(2)
    return builder.as_markup()


def get_enrich_failed_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру повтора или продолжения без обогащения.

    Returns:
        Клавиатура с действиями после сбоя обогащения.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text=PROMPT_RETRY_BUTTON, callback_data=CB_PROMPT_RETRY)
    builder.button(text=PROMPT_FALLBACK_BUTTON, callback_data=CB_PROMPT_FALLBACK)
    builder.adjust(2)
    return builder.as_markup()


def get_title_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру выбора названия песни.

    Returns:
        Клавиатура с кнопками названия по умолчанию и отмены.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="Оставить как есть", callback_data=CB_TITLE_LEAVE_AS_IS)
    builder.button(text=PROMPT_CANCEL_BUTTON, callback_data=CB_PROMPT_CANCEL)
    builder.adjust(2)
    return builder.as_markup()
