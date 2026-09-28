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
from taskiq import Context, TaskiqDepends

from core.utils.error_notify import notify_owner
from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
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
    NOTIFY_SAVE_PROMPT_FAILED_CTX,
    PROMPT_MARKERS,
    PROMPT_TOO_LONG_MSG,
    RETURN_TO_START,
    RUN_AGAIN,
    WAITING_PROMPT_EDITS,
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
)
from domains.enricher.service import format_enriched_prompt, save_enriched_prompt
from domains.enricher.state_models import EnrichmentFlowState
from domains.generation.fsm import MAX_PROMPT_LEN
from shared.contracts.commands import StartEnrichment
from shared.contracts.events import EnrichmentCompleted, GenerationFailed
from shared.domain_ports import generation_flow_starter
from shared.ports.telegram import TelegramPort

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
    flow_state = await get_fsm_data(state=state, model_class=EnrichmentFlowState)
    return flow_state.enrich_id == enrich_id


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
        chat_id: ID чата для публикации результата.
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


async def _publish_enrich_command(command: StartEnrichment) -> None:
    """Публикует команду обогащения в очередь брокера.

    Args:
        command: Команда с параметрами обогащения.
    """
    from domains.enricher.worker import enrich_prompt_task

    await enrich_prompt_task.kiq(command.model_dump())


async def _enrich_and_present(status: Message, state: FSMContext, enrich_id: str, uid: int, chat_id: int) -> None:
    """Публикует команду обогащения в очередь.

    Args:
        status: Сообщение-лоадер (не используется, оставлено для совместимости).
        state: FSM-контекст пользователя.
        enrich_id: Идентификатор текущего запуска (не используется в новой версии).
        uid: Telegram user_id пользователя.
    """
    flow_state = await get_fsm_data(state=state, model_class=EnrichmentFlowState)
    prompt = flow_state.prompt or ""
    enriched_prev = flow_state.enriched_prompt
    edits_text = flow_state.pending_edits

    # Формируем историю для API, если есть правки
    history = None
    if edits_text and enriched_prev:
        history = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": enriched_prev},
        ]
        prompt_to_send = edits_text
    else:
        prompt_to_send = prompt

    # Публикуем команду в очередь
    command = StartEnrichment(
        user_id=uid,
        chat_id=chat_id,
        prompt=prompt_to_send,
        history=history,
    )
    await _publish_enrich_command(command)
    log.info("Enrichment command published (user=%s, has_history=%s)", uid, bool(history))


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

    flow_state = await get_fsm_data(state=state, model_class=EnrichmentFlowState)
    if flow_state.enriching:
        await message.answer(text=ENRICH_IN_PROGRESS_MSG)
        return

    enrich_id = uuid4().hex
    flow_state.prompt = prompt
    flow_state.enriched_prompt = None
    flow_state.pending_edits = None
    flow_state.enriching = True
    flow_state.enrich_id = enrich_id
    await update_fsm_data(state=state, model=flow_state)

    status = await message.answer(text=ENRICH_STARTS_MSG)
    await _enrich_and_present(
        status=status,
        state=state,
        enrich_id=enrich_id,
        uid=message.from_user.id,
        chat_id=message.chat.id,
    )


@router.callback_query(PromptEnricherStates.waiting_for_approval, F.data == CB_PROMPT_APPROVE)
async def handle_prompt_approve(callback: CallbackQuery, state: FSMContext):
    """Подтверждает результат и запрашивает название песни.

    Args:
        callback: Нажатие на кнопку подтверждения.
        state: FSM-контекст пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    flow_state = await get_fsm_data(state=state, model_class=EnrichmentFlowState)
    enriched = flow_state.enriched_prompt
    if not enriched:
        await callback.answer(text=RUN_AGAIN, show_alert=True)
        await state.clear()
        return

    await _save_feedback_best_effort(
        status=callback.message,
        uid=callback.from_user.id,
        initial_prompt=flow_state.prompt or "",
        enriched_prompt=enriched,
    )

    final_prompt = _build_generation_prompt(text=enriched)
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await generation_flow_starter.start_title_input(
        message=callback.message,
        state=state,
        prompt=final_prompt,
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

    flow_state = await get_fsm_data(state=state, model_class=EnrichmentFlowState)
    if not flow_state.prompt:
        await message.answer(
            text=ENRICH_SESSION_FAILURE,
            reply_markup=get_main_keyboard(),
        )
        await state.clear()
        return
    if flow_state.enriching:
        await message.answer(text=ENRICH_IN_PROGRESS_MSG)
        return

    enrich_id = uuid4().hex
    flow_state.pending_edits = edits_text
    flow_state.enriching = True
    flow_state.enrich_id = enrich_id
    await update_fsm_data(state=state, model=flow_state)

    status = await message.answer(text=ENRICH_STARTS_WITH_EDITS)
    await _enrich_and_present(
        status=status,
        state=state,
        enrich_id=enrich_id,
        uid=message.from_user.id,
        chat_id=message.chat.id,
    )


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

    flow_state = await get_fsm_data(state=state, model_class=EnrichmentFlowState)
    if flow_state.enriching:
        await callback.answer(text=ENRICH_RETRY_IN_PROGRESS_MSG, show_alert=True)
        return
    if not flow_state.prompt:
        await callback.answer(text=RUN_AGAIN, show_alert=True)
        await state.clear()
        return

    await callback.answer()
    enrich_id = uuid4().hex
    flow_state.enriching = True
    flow_state.enrich_id = enrich_id
    await update_fsm_data(state=state, model=flow_state)

    with contextlib.suppress(Exception):
        await callback.message.edit_text(text=ENRICH_STARTS_MSG)
    await _enrich_and_present(
        status=callback.message,
        state=state,
        enrich_id=enrich_id,
        uid=callback.from_user.id,
        chat_id=callback.from_user.id,
    )


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

    flow_state = await get_fsm_data(state=state, model_class=EnrichmentFlowState)
    prompt = flow_state.prompt
    if not prompt:
        await callback.answer(text=RUN_AGAIN, show_alert=True)
        await state.clear()
        return

    await callback.answer()
    final_text = flow_state.enriched_prompt or prompt
    await _save_feedback_best_effort(
        status=callback.message,
        uid=callback.from_user.id,
        initial_prompt=prompt,
        enriched_prompt=final_text,
    )
    final_prompt = _build_generation_prompt(text=final_text)
    log.info("Enrichment fallback used (user=%s)", callback.from_user.id)
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await generation_flow_starter.start_title_input(
        message=callback.message,
        state=state,
        prompt=final_prompt,
    )


@router.callback_query(
    StateFilter(PromptEnricherStates.waiting_for_approval),
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


async def handle_enrichment_completed_event(
    event: EnrichmentCompleted,
    context: Context = TaskiqDepends(),
) -> None:
    """Обрабатывает событие успешного обогащения промпта.

    Args:
        event: Событие с обогащённым промптом.
        context: Контекст TaskIQ с зависимостями.
    """
    from aiogram.fsm.storage.base import StorageKey

    state_dict = getattr(context, "state", getattr(context, "dependencies", {}))
    telegram: TelegramPort = state_dict["telegram_port"]
    storage = state_dict["storage"]
    bot = state_dict["bot"]

    # Получаем FSM-контекст пользователя
    fsm_context = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=event.chat_id, user_id=event.user_id),
    )

    # Проверяем, что пользователь всё ещё в процессе обогащения
    current_state = await fsm_context.get_state()
    valid_states = (
        PromptEnricherStates.waiting_for_idea.state,
        PromptEnricherStates.waiting_for_edits.state,
    )
    if current_state not in valid_states:
        log.info("Enrichment result dropped: state changed (user=%s)", event.user_id)
        return

    # Обновляем состояние
    flow_state = await get_fsm_data(state=fsm_context, model_class=EnrichmentFlowState)
    flow_state.enriched_prompt = event.enriched_prompt
    flow_state.enriching = False
    await update_fsm_data(state=fsm_context, model=flow_state)
    await fsm_context.set_state(PromptEnricherStates.waiting_for_approval)

    # Отправляем результат пользователю
    display_text = format_enriched_prompt(raw=event.enriched_prompt)
    await telegram.send_message(
        chat_id=event.chat_id,
        text=ENRICH_RESULT_MSG.format(display_text=html.escape(display_text)),
        reply_markup=get_prompt_approval_keyboard(),
    )
    log.info("Enrichment result delivered (user=%s)", event.user_id)


async def handle_generation_failed_event(
    event: GenerationFailed,
    context: Context = TaskiqDepends(),
) -> None:
    """Обрабатывает событие сбоя генерации или обогащения.

    Args:
        event: Событие с описанием ошибки.
        context: Контекст TaskIQ с зависимостями.
    """
    from aiogram.fsm.storage.base import StorageKey

    state_dict = getattr(context, "state", getattr(context, "dependencies", {}))
    telegram: TelegramPort = state_dict["telegram_port"]
    storage = state_dict["storage"]
    bot = state_dict["bot"]

    fsm_context = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=event.chat_id, user_id=event.user_id),
    )

    # Проверяем стадию ошибки
    if event.stage == "enrichment":
        # Обновляем флаг enriching
        flow_state = await get_fsm_data(state=fsm_context, model_class=EnrichmentFlowState)
        flow_state.enriching = False
        await update_fsm_data(state=fsm_context, model=flow_state)

        # Отправляем сообщение с кнопкой повтора
        await telegram.send_message(
            chat_id=event.chat_id,
            text=ENRICH_FAIL,
            reply_markup=get_enrich_failed_keyboard(),
        )
        log.info("Enrichment failure delivered (user=%s)", event.user_id)
    else:
        # Для других стадий просто очищаем состояние
        await fsm_context.clear()
        await telegram.send_message(
            chat_id=event.chat_id,
            text=f"❌ {event.error_message}",
        )
