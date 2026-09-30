"""Обработчики ввода отзыва и завершения сценария без отзыва."""

import contextlib
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.config import settings
from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
from domains.base.keyboards import get_main_keyboard
from domains.evaluation.evaluation_messages import FEEDBACK_CHOICE_TEXT
from domains.feedback.feedback_messages import FEEDBACK_PROMPT_TEXT, FEEDBACK_THANKS_TEXT
from domains.feedback.fsm import FeedbackStates
from domains.feedback.keyboards import (
    CB_FEEDBACK_FINISH,
    CB_FEEDBACK_SEND,
    get_feedback_finish_keyboard,
    get_feedback_keyboard,
)
from domains.feedback.service import save_feedback
from domains.feedback.state_models import FeedbackFlowState

log = logging.getLogger(__name__)

router = Router()


def _extract_callback_gen_id(callback_data: str | None) -> int | None:
    """Извлекает gen_id из callback_data вида 'fb:action:gen_id'.

    Args:
        callback_data: Строка данных callback-запроса.

    Returns:
        Целочисленный gen_id или None.
    """
    if not callback_data:
        return None
    parts = callback_data.split(":")
    if len(parts) > 2 and parts[2].isdigit():
        return int(parts[2])
    return None


async def _finish_feedback(
    user_id: int,
    state: FSMContext,
    feedback_text: str | None = None,
    gen_id: int | None = None,
) -> None:
    """Сохраняет оценку и/или отзыв, нормализует текст и очищает FSM.

    Args:
        user_id: Telegram user_id пользователя.
        state: FSM-контекст пользователя.
        feedback_text: Текст отзыва, если пользователь прислал его сообщением.
        gen_id: ID генерации для сохранения (опционально, иначе из FSM).

    Returns:
        None.
    """
    flow_state = await get_fsm_data(state=state, model_class=FeedbackFlowState)
    target_gen_id = gen_id if gen_id is not None else flow_state.gen_id
    feedback = feedback_text if feedback_text is not None else flow_state.feedback_text
    if feedback:
        feedback = feedback.strip()
    if feedback is not None and len(feedback) < settings.MIN_FEEDBACK_TEXT:
        feedback = None

    if target_gen_id is not None:
        await save_feedback(
            gen_id=target_gen_id,
            user_id=user_id,
            feedback=feedback,
            evalue=None,
        )
    else:
        log.warning("Feedback finish attempted without gen_id (user=%s)", user_id)
    await state.clear()


@router.message(FeedbackStates.waiting_for_feedback_choice, F.text & ~F.command)
async def handle_feedback_choice_message(message: Message, state: FSMContext | None = None) -> None:
    """Напоминает выбрать действие кнопками, если пользователь написал текст.

    Args:
        message: Входящее сообщение пользователя.
        state: FSM-контекст пользователя (опционально).
    """
    gen_id = None
    if state is not None:
        flow_state = await get_fsm_data(state=state, model_class=FeedbackFlowState)
        gen_id = flow_state.gen_id

    await message.answer(
        text=FEEDBACK_CHOICE_TEXT,
        reply_markup=get_feedback_keyboard(gen_id=gen_id),
    )


@router.message(FeedbackStates.waiting_feedback, F.text & ~F.command)
async def handle_feedback_message(message: Message, state: FSMContext) -> None:
    """Сохраняет оценку и текст отзыва, благодарит и возвращает в начало.

    Args:
        message: Входящее сообщение пользователя.
        state: FSM-контекст пользователя.
    """
    flow_state = await get_fsm_data(state=state, model_class=FeedbackFlowState)
    prompt_message_id = flow_state.feedback_prompt_message_id

    await _finish_feedback(
        user_id=message.from_user.id,
        state=state,
        feedback_text=message.text,
        gen_id=flow_state.gen_id,
    )

    if prompt_message_id is not None:
        with contextlib.suppress(Exception):
            await message.bot.edit_reply_markup(
                chat_id=message.chat.id,
                message_id=prompt_message_id,
                reply_markup=None,
            )

    await message.answer(
        text=FEEDBACK_THANKS_TEXT,
        reply_markup=get_main_keyboard(),
    )


async def _show_feedback_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Переводит сценарий в ожидание текста отзыва и убирает кнопку отправки.

    Args:
        callback: Нажатие кнопки «Отправить фидбек».
        state: FSM-контекст пользователя.
    """
    callback_gen_id = _extract_callback_gen_id(callback.data)
    flow_state = await get_fsm_data(state=state, model_class=FeedbackFlowState)
    if callback_gen_id is not None and flow_state.gen_id is not None and callback_gen_id != flow_state.gen_id:
        log.warning(
            "Feedback callback gen_id mismatch (callback=%s, state=%s, user=%s)",
            callback_gen_id,
            flow_state.gen_id,
            callback.from_user.id,
        )
        await callback.answer(text="Этот запрос отзыва относится к устаревшей генерации.", show_alert=True)
        return

    gen_id = callback_gen_id or flow_state.gen_id
    await callback.message.edit_text(
        text=FEEDBACK_PROMPT_TEXT,
        reply_markup=get_feedback_finish_keyboard(gen_id=gen_id),
    )
    await state.set_state(FeedbackStates.waiting_feedback)

    flow_state.gen_id = gen_id
    flow_state.feedback_text = None
    flow_state.feedback_prompt_message_id = callback.message.message_id
    await update_fsm_data(state=state, model=flow_state)

    await callback.answer()


@router.callback_query(
    FeedbackStates.waiting_for_feedback_choice,
    F.data.startswith(CB_FEEDBACK_SEND),
)
async def handle_feedback_send_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Просит написать отзыв и показывает только кнопку завершения без отзыва.

    Args:
        callback: Нажатие кнопки «Отправить фидбек» на этапе выбора.
        state: FSM-контекст пользователя.
    """
    await _show_feedback_prompt(callback=callback, state=state)


@router.callback_query(FeedbackStates.waiting_feedback, F.data.startswith(CB_FEEDBACK_SEND))
async def handle_feedback_send(callback: CallbackQuery, state: FSMContext) -> None:
    """Повторно показывает ожидание отзыва, если старая кнопка ещё видна.

    Args:
        callback: Нажатие кнопки «Отправить фидбек» в состоянии ожидания отзыва.
        state: FSM-контекст пользователя.
    """
    await _show_feedback_prompt(callback=callback, state=state)


async def _finish_from_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """Завершает сценарий по нажатию кнопки: убирает markup, сохраняет и благодарит.

    Args:
        callback: Нажатие кнопки «Завершить без отзыва».
        state: FSM-контекст пользователя.
    """
    callback_gen_id = _extract_callback_gen_id(callback.data)
    flow_state = await get_fsm_data(state=state, model_class=FeedbackFlowState)
    if callback_gen_id is not None and flow_state.gen_id is not None and callback_gen_id != flow_state.gen_id:
        log.warning(
            "Feedback callback gen_id mismatch (callback=%s, state=%s, user=%s)",
            callback_gen_id,
            flow_state.gen_id,
            callback.from_user.id,
        )
        await callback.answer(text="Этот запрос отзыва относится к устаревшей генерации.", show_alert=True)
        return

    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)

    gen_id = callback_gen_id or flow_state.gen_id
    await _finish_feedback(user_id=callback.from_user.id, state=state, gen_id=gen_id)
    await callback.message.answer(
        text=FEEDBACK_THANKS_TEXT,
        reply_markup=get_main_keyboard(),
    )
    await callback.answer()


@router.callback_query(
    FeedbackStates.waiting_for_feedback_choice,
    F.data.startswith(CB_FEEDBACK_FINISH),
)
async def handle_feedback_finish_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Сохраняет оценку без отзыва на этапе выбора и возвращает в начало.

    Args:
        callback: Нажатие кнопки «Завершить без отзыва» на этапе выбора.
        state: FSM-контекст пользователя.
    """
    await _finish_from_callback(callback=callback, state=state)


@router.callback_query(FeedbackStates.waiting_feedback, F.data.startswith(CB_FEEDBACK_FINISH))
async def handle_feedback_finish(callback: CallbackQuery, state: FSMContext) -> None:
    """Сохраняет оценку без отзыва, благодарит пользователя и возвращает в начало.

    Args:
        callback: Нажатие кнопки «Завершить без отзыва».
        state: FSM-контекст пользователя.
    """
    await _finish_from_callback(callback=callback, state=state)
