"""Хендлеры сценария обогащения промпта."""

import contextlib
import html
import logging
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError

from core.utils.error_notify import notify_owner
from domains.auth.service import is_authorized
from domains.base.keyboards import get_cancel_keyboard, get_main_keyboard
from domains.enricher.enricher_messages import (
    ACCESS_DENIED_MSG,
    EMPTY_FIELD_RE,
    EMPTY_PROMPT_EDITS,
    EMPTY_PROMPT_MSG,
    EMPTY_TEMPLATE_MSG,
    ENRICH_CANCELED,
    ENRICH_FAIL,
    ENRICH_IN_PROGRESS_MSG,
    ENRICH_RESULT_MSG,
    ENRICH_RETRY_IN_PROGRESS_MSG,
    ENRICH_SESSION_FAILURE,
    ENRICH_STARTS_MSG,
    ENRICH_STARTS_WITH_EDITS,
    ENRICHER_IS_BROKEN,
    NOTIFY_ENRICHER_NOT_CONFIGURED_CTX,
    NOTIFY_SAVE_PROMPT_FAILED_CTX,
    PROMPT_MARKERS,
    PROMPT_TOO_LONG_MSG,
    RETURN_TO_START,
    RUN_AGAIN,
    WAITING_PROMPT_EDITS,
    WAITING_TITLE_MSG,
)
from domains.enricher.fsm import PromptEnricherStates
from domains.enricher.keyboards import (
    CB_PROMPT_APPROVE,
    CB_PROMPT_CANCEL,
    CB_PROMPT_EDIT,
    CB_PROMPT_FALLBACK,
    CB_PROMPT_RETRY,
    get_enrich_failed_keyboard,
    get_prompt_approval_keyboard,
    get_title_keyboard,
)
from domains.enricher.service import enrich_prompt, format_enriched_prompt, save_enriched_prompt
from domains.generation.fsm import MAX_PROMPT_LEN, GenerationStates

log = logging.getLogger(__name__)

router = Router()


def _build_generation_prompt(text: str) -> str:
    """Оборачивает пользовательский текст в промпт для генерации.

    Args:
        text: Обогащённый или исходный текст описания песни.

    Returns:
        Промпт для сервиса генерации.
    """
    if any(marker in text for marker in PROMPT_MARKERS):
        return (
            "Create a song based on the following brief. "
            "If the lyrics are provided in Russian, sing in Russian.\n\n"
            f"{text}"
        )
    return f"Create a song based on these lyrics. If the lyrics are in Russian, sing in Russian.\n\n{text}"


async def _is_actual_enrich(state: FSMContext, enrich_id: str) -> bool:
    """Проверяет, что запуск обогащения всё ещё актуален.

    Args:
        state: FSM-контекст пользователя.
        enrich_id: Идентификатор запуска.

    Returns:
        True, если идентификатор совпадает с сохранённым в FSM.
    """
    return (await state.get_data()).get("enrich_id") == enrich_id


async def _ensure_callback_authorized(callback: CallbackQuery, state: FSMContext) -> bool:
    """Проверяет авторизацию пользователя для callback-события.

    Args:
        callback: Нажатие на inline-кнопку.
        state: FSM-контекст пользователя.

    Returns:
        True, если пользователь авторизован.
    """
    if await is_authorized(callback.from_user.id):
        return True
    await callback.answer(text=ACCESS_DENIED_MSG, show_alert=True)
    await state.clear()
    return False


async def _save_feedback_best_effort(status: Message, uid: int, initial_prompt: str, enriched_prompt: str) -> None:
    """Сохраняет промпты в БД, не прерывая пользовательский сценарий.

    Args:
        status: Сообщение, используемое для уведомления владельца.
        uid: Telegram user_id пользователя.
        initial_prompt: Исходный промпт.
        enriched_prompt: Обогащённый промпт.
    """
    try:
        await save_enriched_prompt(tg_id=uid, initial_prompt=initial_prompt, enriched_prompt=enriched_prompt)
    except (SQLAlchemyError, ValueError) as error:
        log.error("Failed to save enriched prompt (user=%s): %s", uid, error)
        with contextlib.suppress(Exception):
            await notify_owner(
                bot=status.bot,
                context=NOTIFY_SAVE_PROMPT_FAILED_CTX.format(uid=uid),
                err=error,
            )


async def _enrich_and_present(status: Message, state: FSMContext, enrich_id: str, uid: int) -> None:
    """Запускает обогащение и показывает результат или действия после сбоя.

    Args:
        status: Сообщение-лоадер для редактирования.
        state: FSM-контекст пользователя.
        enrich_id: Идентификатор текущего запуска.
        uid: Telegram user_id пользователя.
    """
    data = await state.get_data()
    prompt = data.get("prompt", "")
    enriched_prev = data.get("enriched_prompt")
    edits_text = data.get("pending_edits")

    try:
        if edits_text and enriched_prev:
            history = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": enriched_prev},
            ]
            result = await enrich_prompt(prompt=edits_text, history=history)
        else:
            result = await enrich_prompt(prompt=prompt)
    except ValueError as error:
        if await _is_actual_enrich(state=state, enrich_id=enrich_id):
            await state.update_data(enriching=False)
        with contextlib.suppress(Exception):
            await notify_owner(
                bot=status.bot,
                context=NOTIFY_ENRICHER_NOT_CONFIGURED_CTX.format(uid=uid),
                err=error,
            )
        with contextlib.suppress(Exception):
            await status.edit_text(
                text=ENRICHER_IS_BROKEN,
                reply_markup=get_enrich_failed_keyboard(),
            )
        return

    if not await _is_actual_enrich(state=state, enrich_id=enrich_id):
        log.info("Stale enrichment result dropped (user=%s)", uid)
        return

    await state.update_data(enriching=False)

    if result is None:
        with contextlib.suppress(Exception):
            await status.edit_text(
                text=ENRICH_FAIL,
                reply_markup=get_enrich_failed_keyboard(),
            )
        return

    await state.update_data(enriched_prompt=result)
    log.info("Enrichment succeeded (user=%s, edits=%s)", uid, bool(edits_text))
    display_text = format_enriched_prompt(raw=result)

    with contextlib.suppress(Exception):
        await status.edit_text(
            text=ENRICH_RESULT_MSG.format(display_text=html.escape(display_text)),
            reply_markup=get_prompt_approval_keyboard(),
        )
    await state.set_state(PromptEnricherStates.waiting_for_approval)


@router.message(PromptEnricherStates.waiting_for_idea, F.text)
async def handle_idea(message: Message, state: FSMContext):
    """Принимает описание песни и запускает обогащение.

    Args:
        message: Сообщение с описанием песни.
        state: FSM-контекст пользователя.
    """
    prompt = message.text.strip()

    if not prompt:
        await message.answer(text=EMPTY_PROMPT_MSG)
        return
    if len(prompt) > MAX_PROMPT_LEN:
        await message.answer(text=PROMPT_TOO_LONG_MSG.format(length=len(prompt), max_len=MAX_PROMPT_LEN))
        return

    if not EMPTY_FIELD_RE.sub("", prompt).strip():
        await message.answer(text=EMPTY_TEMPLATE_MSG)
        return

    data = await state.get_data()
    if data.get("enriching"):
        await message.answer(text=ENRICH_IN_PROGRESS_MSG)
        return

    enrich_id = uuid4().hex
    await state.update_data(
        prompt=prompt,
        enriched_prompt=None,
        pending_edits=None,
        enriching=True,
        enrich_id=enrich_id,
    )
    status = await message.answer(text=ENRICH_STARTS_MSG)
    await _enrich_and_present(status=status, state=state, enrich_id=enrich_id, uid=message.from_user.id)


@router.callback_query(PromptEnricherStates.waiting_for_approval, F.data == CB_PROMPT_APPROVE)
async def handle_prompt_approve(callback: CallbackQuery, state: FSMContext):
    """Подтверждает результат и запрашивает название песни.

    Args:
        callback: Нажатие на кнопку подтверждения.
        state: FSM-контекст пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    data = await state.get_data()
    enriched = data.get("enriched_prompt")
    if not enriched:
        await callback.answer(text=RUN_AGAIN, show_alert=True)
        await state.clear()
        return

    await _save_feedback_best_effort(
        status=callback.message,
        uid=callback.from_user.id,
        initial_prompt=data.get("prompt"),
        enriched_prompt=enriched,
    )

    await state.update_data(prompt=_build_generation_prompt(text=enriched))
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(GenerationStates.waiting_for_title)
    await callback.message.answer(
        text=WAITING_TITLE_MSG,
        reply_markup=get_title_keyboard(),
    )


@router.callback_query(PromptEnricherStates.waiting_for_approval, F.data == CB_PROMPT_EDIT)
async def handle_prompt_edit(callback: CallbackQuery, state: FSMContext):
    """Переводит сценарий в ожидание правок.

    Args:
        callback: Нажатие на кнопку правки.
        state: FSM-контекст пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    await state.set_state(PromptEnricherStates.waiting_for_edits)
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        text=WAITING_PROMPT_EDITS,
        reply_markup=get_cancel_keyboard(),
    )


@router.message(PromptEnricherStates.waiting_for_edits, F.text)
async def handle_prompt_edits(message: Message, state: FSMContext):
    """Принимает правки и повторно запускает обогащение.

    Args:
        message: Сообщение с правками.
        state: FSM-контекст пользователя.
    """
    edits_text = message.text.strip()

    if not edits_text:
        await message.answer(text=EMPTY_PROMPT_EDITS)
        return
    if len(edits_text) > MAX_PROMPT_LEN:
        await message.answer(text=PROMPT_TOO_LONG_MSG.format(length=len(edits_text), max_len=MAX_PROMPT_LEN))
        return

    data = await state.get_data()
    if not data.get("prompt"):
        await message.answer(
            text=ENRICH_SESSION_FAILURE,
            reply_markup=get_main_keyboard(),
        )
        await state.clear()
        return
    if data.get("enriching"):
        await message.answer(text=ENRICH_IN_PROGRESS_MSG)
        return

    enrich_id = uuid4().hex
    await state.update_data(pending_edits=edits_text, enriching=True, enrich_id=enrich_id)
    status = await message.answer(text=ENRICH_STARTS_WITH_EDITS)
    await _enrich_and_present(status=status, state=state, enrich_id=enrich_id, uid=message.from_user.id)


@router.callback_query(
    StateFilter(PromptEnricherStates.waiting_for_idea, PromptEnricherStates.waiting_for_edits),
    F.data == CB_PROMPT_RETRY,
)
async def handle_prompt_retry(callback: CallbackQuery, state: FSMContext):
    """Повторно запускает обогащение после сбоя.

    Args:
        callback: Нажатие на кнопку повтора.
        state: FSM-контекст пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    data = await state.get_data()
    if data.get("enriching"):
        await callback.answer(text=ENRICH_RETRY_IN_PROGRESS_MSG, show_alert=True)
        return
    if not data.get("prompt"):
        await callback.answer(text=RUN_AGAIN, show_alert=True)
        await state.clear()
        return

    await callback.answer()
    enrich_id = uuid4().hex
    await state.update_data(enriching=True, enrich_id=enrich_id)
    with contextlib.suppress(Exception):
        await callback.message.edit_text(text=ENRICH_STARTS_MSG)
    await _enrich_and_present(status=callback.message, state=state, enrich_id=enrich_id, uid=callback.from_user.id)


@router.callback_query(
    StateFilter(PromptEnricherStates.waiting_for_idea, PromptEnricherStates.waiting_for_edits),
    F.data == CB_PROMPT_FALLBACK,
)
async def handle_prompt_fallback(callback: CallbackQuery, state: FSMContext):
    """Продолжает сценарий без обогащения после сбоя.

    Args:
        callback: Нажатие на кнопку продолжения без обогащения.
        state: FSM-контекст пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    data = await state.get_data()
    prompt = data.get("prompt")
    if not prompt:
        await callback.answer(text=RUN_AGAIN, show_alert=True)
        await state.clear()
        return

    await callback.answer()
    final_text = data.get("enriched_prompt") or prompt
    await _save_feedback_best_effort(
        status=callback.message,
        uid=callback.from_user.id,
        initial_prompt=prompt,
        enriched_prompt=final_text,
    )
    await state.update_data(prompt=_build_generation_prompt(text=final_text))
    log.info("Enrichment fallback used (user=%s)", callback.from_user.id)
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(GenerationStates.waiting_for_title)
    await callback.message.answer(
        text=WAITING_TITLE_MSG,
        reply_markup=get_title_keyboard(),
    )


@router.callback_query(
    StateFilter(PromptEnricherStates.waiting_for_approval, GenerationStates.waiting_for_title),
    F.data == CB_PROMPT_CANCEL,
)
async def handle_prompt_cancel(callback: CallbackQuery, state: FSMContext):
    """Отменяет сценарий обогащения и возвращает пользователя в меню.

    Args:
        callback: Нажатие на кнопку отмены.
        state: FSM-контекст пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    await state.clear()
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_text(text=ENRICH_CANCELED)
    await callback.message.answer(text=RETURN_TO_START, reply_markup=get_main_keyboard())
