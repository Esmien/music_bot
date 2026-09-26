"""Хендлеры точки входа генерации: запуск, повтор после сбоя и название песни."""

import contextlib

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from domains.auth.handlers import _require_auth
from domains.auth.service import is_authorized
from domains.base.keyboards import GENERATE_BUTTON, get_cancel_keyboard, get_main_keyboard
from domains.enricher.enricher_messages import PROMPT_HINT, PROMPT_TEMPLATE
from domains.enricher.fsm import PromptEnricherStates
from domains.enricher.keyboards import CB_TITLE_LEAVE_AS_IS
from domains.generation.fsm import MAX_TITLE_LEN, GenerationStates
from domains.generation.generation_messages import (
    ACCESS_DENIED_TEXT,
    DEFAULT_TITLE,
    EMPTY_TITLE_TEXT,
    ENRICHMENT_IN_PROGRESS_TEXT,
    GENERATION_ALREADY_RUNNING_TEXT,
    GENERATION_CANCEL_WAIT_TEXT,
    PROMPT_LOST_TEXT,
    RESTART_GENERATION_TEXT,
    TITLE_TOO_LONG_TEXT,
)
from domains.generation.pipeline_handlers import generate_and_send

router = Router(name="generation")


@router.message(F.text == GENERATE_BUTTON)
async def cmd_generate(message: Message, state: FSMContext):
    """Старт генерации: показывает подсказку и запрашивает описание песни.

    Args:
        message: Входящее сообщение (кнопка генерации).
        state: FSM-контекст текущего пользователя.
    """
    if not await _require_auth(message):
        return

    data = await state.get_data()
    if data.get("generating"):
        await message.answer(text=GENERATION_CANCEL_WAIT_TEXT)
        return
    if data.get("enriching"):
        await message.answer(text=ENRICHMENT_IN_PROGRESS_TEXT)
        return

    await message.answer(text=PROMPT_HINT, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await message.answer(text=f"<code>{PROMPT_TEMPLATE}</code>", parse_mode="HTML")
    await state.set_state(PromptEnricherStates.waiting_for_idea)


@router.callback_query(F.data == "retry_generation")
async def retry_generation(callback: CallbackQuery, state: FSMContext):
    """Повторяет генерацию с сохранёнными prompt и title после сбоя.

    Args:
        callback: Нажатие на кнопку повтора.
        state: FSM-контекст текущего пользователя.
    """
    if not await is_authorized(uid=callback.from_user.id):
        await callback.answer(text=ACCESS_DENIED_TEXT, show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    if data.get("generating"):
        await callback.answer(text=GENERATION_ALREADY_RUNNING_TEXT, show_alert=True)
        return

    prompt = data.get("prompt")
    title = data.get("title", DEFAULT_TITLE)
    if not prompt or not prompt.strip():
        await callback.answer(text=RESTART_GENERATION_TEXT, show_alert=True)
        await state.clear()
        return

    with contextlib.suppress(Exception):
        await callback.message.delete()
    await callback.answer()
    await generate_and_send(
        message=callback.message,
        state=state,
        prompt=prompt,
        title=title,
        user_id=callback.from_user.id,
    )


@router.message(GenerationStates.waiting_for_title, F.text)
async def handle_title(message: Message, state: FSMContext):
    """Принимает название, запускает генерацию и отправляет аудиофайл.

    Args:
        message: Сообщение с названием песни.
        state: FSM-контекст текущего пользователя.
    """
    title = message.text.strip()
    if not title:
        await message.answer(text=EMPTY_TITLE_TEXT)
        return
    if len(title) > MAX_TITLE_LEN:
        await message.answer(text=TITLE_TOO_LONG_TEXT.format(max_title_len=MAX_TITLE_LEN))
        return

    data = await state.get_data()
    if data.get("generating"):
        await message.answer(text=GENERATION_CANCEL_WAIT_TEXT)
        return

    prompt = data.get("prompt")
    if not prompt or not prompt.strip():
        await message.answer(
            text=PROMPT_LOST_TEXT,
            reply_markup=get_main_keyboard(),
        )
        await state.clear()
        return

    await state.update_data(title=title)
    await generate_and_send(message=message, state=state, prompt=prompt, title=title, user_id=message.from_user.id)


@router.callback_query(GenerationStates.waiting_for_title, F.data == CB_TITLE_LEAVE_AS_IS)
async def handle_title_leave_as_is(callback: CallbackQuery, state: FSMContext):
    """Использует название по умолчанию.

    Args:
        callback: Нажатие на кнопку «Оставить как есть».
        state: FSM-контекст текущего пользователя.
    """
    if not await is_authorized(uid=callback.from_user.id):
        await callback.answer(text=ACCESS_DENIED_TEXT, show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    if data.get("generating"):
        await callback.answer(text=GENERATION_ALREADY_RUNNING_TEXT, show_alert=True)
        return

    prompt = data.get("prompt")
    if not prompt or not prompt.strip():
        await callback.answer(text=RESTART_GENERATION_TEXT, show_alert=True)
        await state.clear()
        return

    title = DEFAULT_TITLE
    await state.update_data(title=title)
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.delete()
    await generate_and_send(
        message=callback.message,
        state=state,
        prompt=prompt,
        title=title,
        user_id=callback.from_user.id,
    )
