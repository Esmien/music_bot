"""Обработчики обязательной оценки сгенерированной композиции."""

import contextlib
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Message
from taskiq import Context, TaskiqDepends

from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
from domains.evaluation.evaluation_messages import EVALUATION_PROMPT_TEXT, FEEDBACK_CHOICE_TEXT
from domains.evaluation.keyboards import CB_FEEDBACK_DISLIKE, CB_FEEDBACK_LIKE, get_evaluation_keyboard
from domains.evaluation.service import save_evaluation
from domains.feedback.fsm import FeedbackStates
from domains.feedback.keyboards import get_feedback_keyboard
from domains.feedback.state_models import FeedbackFlowState
from domains.generation.state_models import GenerationFlowState
from shared.contracts.events import GenerationSucceeded
from shared.ports.telegram import TelegramPort

log = logging.getLogger(__name__)

router = Router()


async def handle_generation_succeeded_event(
    event: GenerationSucceeded,
    context: Context = TaskiqDepends(),
) -> None:
    """Обрабатывает событие успешной генерации песни и запрашивает оценку.

    Args:
        event: Событие с данными завершённой генерации.
        context: Контекст TaskIQ с зависимостями.
    """
    state_dict = getattr(context, "state", None) or getattr(context, "dependencies", None) or {}
    telegram: TelegramPort = state_dict["telegram_port"]
    storage = state_dict.get("storage")
    bot = state_dict.get("bot")

    if bot is None or storage is None:
        log.warning("Bot or storage missing in context for evaluation handling")
        return

    fsm_context = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=event.chat_id, user_id=event.user_id),
    )

    flow_state = await get_fsm_data(state=fsm_context, model_class=GenerationFlowState)
    if flow_state.gen_id != event.gen_id:
        log.info("Evaluation skipped for outdated generation (gen_id=%s)", event.gen_id)
        return

    flow_state.generating = False
    await update_fsm_data(state=fsm_context, model=flow_state)
    await fsm_context.set_state(FeedbackStates.waiting_evaluation)

    await telegram.send_message(
        chat_id=event.chat_id,
        text=EVALUATION_PROMPT_TEXT,
        reply_markup=get_evaluation_keyboard(),
    )
    log.info("Evaluation requested (user=%s, gen_id=%s)", event.user_id, event.gen_id)


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

    await save_evaluation(user_id=callback.from_user.id, is_liked=evaluation)

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
