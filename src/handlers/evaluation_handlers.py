"""Обработчики обязательной оценки сгенерированной композиции."""

import contextlib

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.config import UIConfig
from fsm.evaluation_fsm import FeedbackStates
from keyboards.evaluation_keyboards import CB_FEEDBACK_DISLIKE, CB_FEEDBACK_LIKE, get_evaluation_keyboard
from keyboards.feedback_keyboards import get_feedback_keyboard

router = Router()


@router.message(FeedbackStates.waiting_evaluation, F.text & ~F.command)
async def handle_evaluate_prompt(message: Message) -> None:
    """Рисует клавиатуру оценки, если пользователь написал в состоянии ожидания.

    Args:
        message: Входящее сообщение пользователя.
    """
    await message.answer(
        text=UIConfig.EVALUATION_PROMPT_TEXT,
        reply_markup=get_evaluation_keyboard(),
    )


@router.callback_query(FeedbackStates.waiting_evaluation, F.data.in_((CB_FEEDBACK_LIKE, CB_FEEDBACK_DISLIKE)))
async def handle_evaluate(callback: CallbackQuery, state: FSMContext) -> None:
    """Записывает оценку, переводит в выбор действия и рисует кнопки фидбека.

    Args:
        callback: Нажатие кнопки «нравится» или «не нравится».
        state: FSM-контекст пользователя.
    """
    evaluation = callback.data == CB_FEEDBACK_LIKE
    await state.update_data(feedback_evaluation=evaluation)
    await state.set_state(FeedbackStates.waiting_for_feedback_choice)

    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(
        text=UIConfig.FEEDBACK_CHOICE_TEXT,
        reply_markup=get_feedback_keyboard(),
    )
    await callback.answer()
