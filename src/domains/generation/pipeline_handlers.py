"""Хендлеровый слой конвейера генерации: FSM, статусы, отмена и сбои.

«Железная» логика запуска (пер-пользовательские локи, троттлинг
прогресса, демо/реальный режим) живет в domains/generation/service.py; здесь —
только работа с Telegram, FSM-состоянием и реестром active_tasks.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from uuid import uuid4

from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, Message
from sqlalchemy import select

from core.broker import generation_broker
from core.database.engine import get_session
from core.utils.error_notify import notify_owner
from core.utils.fsm_helpers import get_fsm_data, update_fsm_data
from domains.evaluation.evaluation_messages import EVALUATION_PROMPT_TEXT
from domains.evaluation.fsm import FeedbackStates
from domains.evaluation.keyboards import get_evaluation_keyboard
from domains.generation.generation_messages import (
    GENERATION_FAILURE_TEXT,
    GENERATION_IN_PROGRESS_TEXT,
    GENERATION_OWNER_ERROR_CONTEXT,
    GENERATION_SUCCESS_CAPTION,
)
from domains.generation.keyboards import get_retry_keyboard
from domains.generation.models import Generation, GenerationStatus
from domains.generation.registries.task_registry import register_active_task, unregister_active_task
from domains.generation.service import ProgressCallback, make_throttled_progress, user_generation_lock
from domains.generation.state_models import GenerationFlowState
from shared.contracts.commands import RunGeneration

log = logging.getLogger(__name__)


async def _is_actual_gen(state: FSMContext, gen_id: str) -> bool:
    """Проверяет, актуальна ли генерация в стейте.

    Args:
        state: Состояние FSM пользователя.
        gen_id: ID генерации из контекста.

    Returns:
        True, если актуальна.
    """
    flow_state = await get_fsm_data(state=state, model_class=GenerationFlowState)
    return flow_state.gen_id == gen_id


@dataclass
class GenerationContext:
    """Контекст одного запуска генерации.

    Атрибуты:
        message: Сообщение, от имени которого шлются статусы и аудио.
        state: FSM-контекст пользователя.
        prompt: Подготовленное описание песни.
        title: Название трека (используется в имени файла).
        user_id: Telegram user_id пользователя.
        task: Фоновая задача, в которой крутится генерация.
        gen_id: Маркер запуска; защищает от затирания нового FSM-состояния
            поздно завершившейся старой генерацией.
    """

    message: Message
    state: FSMContext
    prompt: str
    title: str
    user_id: int
    task: asyncio.Task
    gen_id: str = ""


async def generate_and_send(message: Message, state: FSMContext, prompt: str, title: str, user_id: int) -> None:
    """Создаёт запись генерации и ставит её выполнение в очередь.

    Args:
        message: Сообщение пользователя.
        state: FSM-контекст пользователя.
        prompt: Промпт для генерации.
        title: Название песни.
        user_id: Telegram user_id пользователя.
    """
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("generate_and_send must run inside an asyncio Task")

    gen_context = GenerationContext(
        message=message,
        state=state,
        prompt=prompt,
        title=title,
        user_id=user_id,
        task=task,
    )

    async with user_generation_lock(user_id=user_id):
        flow_state = await get_fsm_data(state=state, model_class=GenerationFlowState)
        if flow_state.generating:
            await message.answer(text=GENERATION_IN_PROGRESS_TEXT)
            return

        status = await _start_status(gen_context=gen_context)

        async with get_session() as session:
            generation = Generation(
                prompt=prompt,
                enriched_prompt={"text": prompt},
                title=title,
                user_id=user_id,
                status=GenerationStatus.PENDING,
            )
            session.add(generation)
            await session.flush()
            await session.commit()

        flow_state.generating = True
        flow_state.gen_id = generation.id
        await update_fsm_data(state=state, model=flow_state)

        command = RunGeneration(
            user_id=user_id,
            chat_id=message.chat.id,
            gen_id=generation.id,
            prompt=prompt,
            title=title,
            status_message_id=getattr(status, "message_id", None),
        )
        await generation_broker.kicker(task_name="run_generation").kiq(command)


async def _acquire_slot(gen_context: GenerationContext) -> bool:
    """Атомарно занимает слот генерации пользователя.

    Под пер-пользовательским локом проверяет флаг generating и выставляет
    его вместе с маркером gen_id; регистрирует задачу в active_tasks.

    Args:
        gen_context: Контекст запуска генерации.

    Returns:
        True, если слот занят и генерацию можно запускать.
    """
    async with user_generation_lock(user_id=gen_context.user_id):
        flow_state = await get_fsm_data(state=gen_context.state, model_class=GenerationFlowState)
        if flow_state.generating:
            await gen_context.message.answer(text="⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
            return False

        gen_context.gen_id = uuid4().hex
        flow_state.generating = True
        flow_state.gen_id = gen_context.gen_id
        await update_fsm_data(state=gen_context.state, model=flow_state)
        await register_active_task(uid=gen_context.user_id, task=gen_context.task)
        return True


async def _start_status(gen_context: GenerationContext) -> Message:
    """Показывает «бот отправляет файл…» и создаёт сообщение-статус с прогрессом."""
    await gen_context.message.bot.send_chat_action(
        chat_id=gen_context.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT
    )

    return await gen_context.message.answer(text=GENERATION_IN_PROGRESS_TEXT)


def _make_progress_reporter(status: Message) -> ProgressCallback:
    """Возвращает колбэк on_progress, троттлящий правки статуса.

    Args:
        status: Сообщение-статус, которое редактируется по мере прогресса.

    Returns:
        Колбэк on_progress(stage, fraction) для сервиса генерации.
    """

    async def report(text: str) -> None:
        """Отрисовывает новый текст статуса, глотая сбои правки.

        Args:
            text: Готовый текст статуса с прогресс-баром.
        """
        with contextlib.suppress(Exception):
            await status.edit_text(text)

    return make_throttled_progress(report=report)


async def _cleanup_cancelled(gen_context: GenerationContext, status: Message) -> None:
    """Чистит пользовательский экран после отмены генерации.

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
    """
    with contextlib.suppress(Exception):
        await status.delete()

    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        flow_state = await get_fsm_data(state=gen_context.state, model_class=GenerationFlowState)
        flow_state.generating = False
        await update_fsm_data(state=gen_context.state, model=flow_state)


async def _handle_failure(gen_context: GenerationContext, status: Message, error: Exception) -> None:
    """Обрабатывает сбой сервиса: уведомляет владельца и показывает кнопку повтора.

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
        error: Исключение, упавшее из сервиса генерации.
    """
    with contextlib.suppress(Exception):
        await notify_owner(
            bot=gen_context.message.bot,
            context=GENERATION_OWNER_ERROR_CONTEXT.format(
                user_id=gen_context.user_id,
                title=gen_context.title,
            ),
            err=error,
        )

    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        flow_state = await get_fsm_data(state=gen_context.state, model_class=GenerationFlowState)
        flow_state.generating = False
        await update_fsm_data(state=gen_context.state, model=flow_state)

    with contextlib.suppress(Exception):
        await status.edit_text(
            GENERATION_FAILURE_TEXT,
            reply_markup=get_retry_keyboard(),
        )


async def _deliver_result(gen_context: GenerationContext, status: Message, audio_bytes: bytes) -> None:
    """Отправляет готовое аудио, переводит в оценку и сохраняет название.

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
        audio_bytes: Байты готового аудио.
    """
    is_actual = await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id)
    safe_title = "".join(c if c.isalnum() or c in "_-." else "_" for c in gen_context.title)[:80] or "song"

    file = BufferedInputFile(file=audio_bytes, filename=f"{safe_title}.mp3")
    await gen_context.message.answer_audio(audio=file, caption=GENERATION_SUCCESS_CAPTION)

    if is_actual:
        flow_state = await get_fsm_data(state=gen_context.state, model_class=GenerationFlowState)
        flow_state.generating = False
        await update_fsm_data(state=gen_context.state, model=flow_state)
        await gen_context.state.set_state(FeedbackStates.waiting_evaluation)
        await gen_context.message.answer(
            text=EVALUATION_PROMPT_TEXT,
            reply_markup=get_evaluation_keyboard(),
        )

    await _persist_generated_title(gen_context=gen_context)

    with contextlib.suppress(Exception):
        await status.delete()


async def _persist_generated_title(gen_context: GenerationContext) -> None:
    """Сохраняет название и успешный статус в ожидающую запись генерации.

    Args:
        gen_context: Контекст запуска генерации.
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Generation)
                .where(
                    Generation.user_id == gen_context.user_id,
                    Generation.status == GenerationStatus.PENDING,
                )
                .order_by(Generation.created_at.desc(), Generation.id.desc())
                .limit(1)
            )
            generation = result.scalar_one_or_none()
            if generation is None:
                log.warning("Pending generation record not found (user=%s)", gen_context.user_id)
                return

            generation.title = gen_context.title
            generation.status = GenerationStatus.SUCCESS
            await session.commit()
    except Exception:
        log.exception("Failed to persist generated title (user=%s)", gen_context.user_id, exc_info=True)


async def _release_slot(gen_context: GenerationContext) -> None:
    """Снимает регистрацию задачи в едином реестре активных генераций.

    Args:
        gen_context: Контекст запуска генерации.
    """
    await unregister_active_task(uid=gen_context.user_id, task=gen_context.task)
