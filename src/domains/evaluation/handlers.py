"""Обработчики обязательной оценки сгенерированной композиции."""

import contextlib

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
from domains.evaluation.evaluation_messages import EVALUATION_PROMPT_TEXT, FEEDBACK_CHOICE_TEXT
from domains.evaluation.keyboards import CB_FEEDBACK_DISLIKE, CB_FEEDBACK_LIKE, get_evaluation_keyboard
from domains.feedback.fsm import FeedbackStates
from domains.feedback.keyboards import get_feedback_keyboard
from domains.feedback.state_models import FeedbackFlowState

router = Router()


@router.message(FeedbackStates.waiting_evaluation, F.text & ~F.command)
async def handle_evaluate_prompt(message: Message) -> None:
    """Рисует клавиатуру оценки, если пользователь написал в состоянии ожидания.

    Args:
        message: Входящее сообщение пользователя.
    """
    await message.answer(
        text=EVALUATION_PROMPT_TEXT,
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

    flow_state = await get_fsm_data(state=state, model_class=FeedbackFlowState)
    flow_state.feedback_evaluation = evaluation
    await update_fsm_data(state=state, model=flow_state)

    await state.set_state(FeedbackStates.waiting_for_feedback_choice)

    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(
        text=FEEDBACK_CHOICE_TEXT,
        reply_markup=get_feedback_keyboard(),
    )
    await callback.answer()
