"""Хендлеры точки входа генерации: запуск, повтор после сбоя и название песни."""

import contextlib
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from taskiq import Context, TaskiqDepends

from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
from domains.auth.handlers import require_auth
from domains.auth.service import is_authorized
from domains.base.keyboards import GENERATE_BUTTON, get_main_keyboard
from domains.enricher.keyboards import CB_PROMPT_CANCEL, CB_TITLE_LEAVE_AS_IS
from domains.enricher.state_models import EnrichmentFlowState
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
from domains.generation.state_models import GenerationFlowState
from shared.contracts.events import GenerationFailed
from shared.domain_ports import enrichment_flow_starter

log = logging.getLogger(__name__)

router = Router(name="generation")


@router.message(F.text == GENERATE_BUTTON)
async def cmd_generate(message: Message, state: FSMContext):
    """Старт генерации: показывает подсказку и запрашивает описание песни.

    Args:
        message: Входящее сообщение (кнопка генерации).
        state: FSM-контекст текущего пользователя.
    """
    if not await require_auth(message):
        return

    gen_state = await get_fsm_data(state=state, model_class=GenerationFlowState)
    enrich_state = await get_fsm_data(state=state, model_class=EnrichmentFlowState)

    if gen_state.generating:
        await message.answer(text=GENERATION_CANCEL_WAIT_TEXT)
        return
    if enrich_state.enriching:
        await message.answer(text=ENRICHMENT_IN_PROGRESS_TEXT)
        return

    await enrichment_flow_starter.start_enrichment(message=message, state=state)


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

    flow_state = await get_fsm_data(state=state, model_class=GenerationFlowState)
    if flow_state.generating:
        await callback.answer(text=GENERATION_ALREADY_RUNNING_TEXT, show_alert=True)
        return

    prompt = flow_state.prompt
    title = flow_state.title or DEFAULT_TITLE
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

    from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
    from domains.enricher.enricher_messages import ENRICH_FAIL, ENRICH_FAIL_EXHAUSTED, MAX_ENRICH_ATTEMPTS
    from domains.enricher.keyboards import get_enrich_exhausted_keyboard, get_enrich_failed_keyboard
    from domains.enricher.state_models import EnrichmentFlowState
    from shared.ports.telegram import TelegramPort

    state_dict = getattr(context, "state", None) or getattr(context, "dependencies", None) or {}
    telegram: TelegramPort = state_dict["telegram_port"]
    storage = state_dict["storage"]
    bot = state_dict["bot"]

    fsm_context = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=bot.id, chat_id=event.chat_id, user_id=event.user_id),
    )

    # Проверяем стадию ошибки
    if event.stage == "enrichment":
        flow_state = await get_fsm_data(state=fsm_context, model_class=EnrichmentFlowState)
        flow_state.enriching = False
        flow_state.retry_count += 1
        await update_fsm_data(state=fsm_context, model=flow_state)

        if flow_state.retry_count < MAX_ENRICH_ATTEMPTS:
            await telegram.send_message(
                chat_id=event.chat_id,
                text=ENRICH_FAIL.format(
                    attempt=flow_state.retry_count,
                    max_attempts=MAX_ENRICH_ATTEMPTS,
                ),
                reply_markup=get_enrich_failed_keyboard(),
            )
        else:
            await telegram.send_message(
                chat_id=event.chat_id,
                text=ENRICH_FAIL_EXHAUSTED.format(
                    max_attempts=MAX_ENRICH_ATTEMPTS,
                ),
                reply_markup=get_enrich_exhausted_keyboard(),
            )
        log.info(
            "Enrichment failure delivered (user=%s, attempt=%s/%s)",
            event.user_id,
            flow_state.retry_count,
            MAX_ENRICH_ATTEMPTS,
        )
    else:
        # Для других стадий сбрасываем флаг генерации и очищаем состояние
        flow_state = await get_fsm_data(state=fsm_context, model_class=GenerationFlowState)
        flow_state.generating = False
        await update_fsm_data(state=fsm_context, model=flow_state)
        await fsm_context.set_state(None)
        await telegram.send_message(
            chat_id=event.chat_id,
            text=f"❌ {event.error_message}",
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

    flow_state = await get_fsm_data(state=state, model_class=GenerationFlowState)
    if flow_state.generating:
        await message.answer(text=GENERATION_CANCEL_WAIT_TEXT)
        return

    prompt = flow_state.prompt
    if not prompt or not prompt.strip():
        await message.answer(
            text=PROMPT_LOST_TEXT,
            reply_markup=get_main_keyboard(),
        )
        await state.clear()
        return

    flow_state.title = title
    await update_fsm_data(state=state, model=flow_state)
    await generate_and_send(message=message, state=state, prompt=prompt, title=title, user_id=message.from_user.id)


@router.callback_query(GenerationStates.waiting_for_title, F.data == CB_PROMPT_CANCEL)
async def handle_generation_cancel(callback: CallbackQuery, state: FSMContext):
    """Отменяет ввод названия и возвращает пользователя в меню.

    Args:
        callback: Нажатие на кнопку отмены.
        state: FSM-контекст текущего пользователя.
    """
    if not await is_authorized(uid=callback.from_user.id):
        await callback.answer(text=ACCESS_DENIED_TEXT, show_alert=True)
        await state.clear()
        return

    await state.clear()
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_text(text="Генерация отменена.")
    await callback.message.answer(text=RESTART_GENERATION_TEXT, reply_markup=get_main_keyboard())


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

    flow_state = await get_fsm_data(state=state, model_class=GenerationFlowState)
    if flow_state.generating:
        await callback.answer(text=GENERATION_ALREADY_RUNNING_TEXT, show_alert=True)
        return

    prompt = flow_state.prompt
    if not prompt or not prompt.strip():
        await callback.answer(text=RESTART_GENERATION_TEXT, show_alert=True)
        await state.clear()
        return

    title = DEFAULT_TITLE
    flow_state.title = title
    await update_fsm_data(state=state, model=flow_state)
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
