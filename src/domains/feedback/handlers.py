"""Обработчики ввода отзыва и завершения сценария без отзыва."""

import contextlib

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.config import UIConfig, settings
from domains.base.keyboards import get_main_keyboard
from domains.feedback.fsm import FeedbackStates
from domains.feedback.keyboards import (
    CB_FEEDBACK_FINISH,
    CB_FEEDBACK_SEND,
    get_feedback_finish_keyboard,
    get_feedback_keyboard,
)
from domains.feedback.service import save_feedback

router = Router()


async def _finish_feedback(user_id: int, state: FSMContext, feedback_text: str | None = None) -> None:
    """Сохраняет оценку и/или отзыв, нормализует текст и очищает FSM.

    Args:
        user_id: Telegram user_id пользователя.
        state: FSM-контекст пользователя.
        feedback_text: Текст отзыва, если пользователь прислал его сообщением.

    Returns:
        None.
    """
    data = await state.get_data()
    feedback = feedback_text if feedback_text is not None else data.get("feedback_text")
    if feedback:
        feedback = feedback.strip()
    if feedback is not None and len(feedback) < settings.MIN_FEEDBACK_TEXT:
        feedback = None

    evalue = bool(data.get("feedback_evaluation"))
    await save_feedback(user_id=user_id, feedback=feedback, evalue=evalue)
    await state.clear()


@router.message(FeedbackStates.waiting_for_feedback_choice, F.text & ~F.command)
async def handle_feedback_choice_message(message: Message) -> None:
    """Напоминает выбрать действие кнопками, если пользователь написал текст.

    Args:
        message: Входящее сообщение пользователя.
    """
    await message.answer(
        text=UIConfig.FEEDBACK_CHOICE_TEXT,
        reply_markup=get_feedback_keyboard(),
    )


@router.message(FeedbackStates.waiting_feedback, F.text & ~F.command)
async def handle_feedback_message(message: Message, state: FSMContext) -> None:
    """Сохраняет оценку и текст отзыва, благодарит и возвращает в начало.

    Args:
        message: Входящее сообщение пользователя.
        state: FSM-контекст пользователя.
    """
    data = await state.get_data()
    prompt_message_id = data.get("feedback_prompt_message_id")

    await _finish_feedback(user_id=message.from_user.id, state=state, feedback_text=message.text)

    if prompt_message_id is not None:
        with contextlib.suppress(Exception):
            await message.bot.edit_reply_markup(
                chat_id=message.chat.id,
                message_id=prompt_message_id,
                reply_markup=None,
            )

    await message.answer(
        text=UIConfig.FEEDBACK_THANKS_TEXT,
        reply_markup=get_main_keyboard(),
    )


async def _show_feedback_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Переводит сценарий в ожидание текста отзыва и убирает кнопку отправки.

    Args:
        callback: Нажатие кнопки «Отправить фидбек».
        state: FSM-контекст пользователя.
    """
    await callback.message.edit_text(
        text=UIConfig.FEEDBACK_PROMPT_TEXT,
        reply_markup=get_feedback_finish_keyboard(),
    )
    await state.set_state(FeedbackStates.waiting_feedback)
    await state.update_data(
        feedback_text=None,
        feedback_prompt_message_id=callback.message.message_id,
    )
    await callback.answer()


@router.callback_query(FeedbackStates.waiting_for_feedback_choice, F.data == CB_FEEDBACK_SEND)
async def handle_feedback_send_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Просит написать отзыв и показывает только кнопку завершения без отзыва.

    Args:
        callback: Нажатие кнопки «Отправить фидбек» на этапе выбора.
        state: FSM-контекст пользователя.
    """
    await _show_feedback_prompt(callback=callback, state=state)


@router.callback_query(FeedbackStates.waiting_feedback, F.data == CB_FEEDBACK_SEND)
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
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)

    await _finish_feedback(user_id=callback.from_user.id, state=state)
    await callback.message.answer(
        text=UIConfig.FEEDBACK_THANKS_TEXT,
        reply_markup=get_main_keyboard(),
    )
    await callback.answer()


@router.callback_query(FeedbackStates.waiting_for_feedback_choice, F.data == CB_FEEDBACK_FINISH)
async def handle_feedback_finish_choice(callback: CallbackQuery, state: FSMContext) -> None:
    """Сохраняет оценку без отзыва на этапе выбора и возвращает в начало.

    Args:
        callback: Нажатие кнопки «Завершить без отзыва» на этапе выбора.
        state: FSM-контекст пользователя.
    """
    await _finish_from_callback(callback=callback, state=state)


@router.callback_query(FeedbackStates.waiting_feedback, F.data == CB_FEEDBACK_FINISH)
async def handle_feedback_finish(callback: CallbackQuery, state: FSMContext) -> None:
    """Сохраняет оценку без отзыва, благодарит пользователя и возвращает в начало.

    Args:
        callback: Нажатие кнопки «Завершить без отзыва».
        state: FSM-контекст пользователя.
    """
    await _finish_from_callback(callback=callback, state=state)
