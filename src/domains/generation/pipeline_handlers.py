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

from core.config import UIConfig
from core.database.engine import get_session
from core.utils.error_notify import notify_owner
from domains.evaluation.fsm import FeedbackStates
from domains.evaluation.keyboards import get_evaluation_keyboard
from domains.generation import service as generation_service
from domains.generation.keyboards import get_retry_keyboard
from domains.generation.models import Generation, GenerationStatus
from domains.generation.registries.task_registry import register_active_task, unregister_active_task
from domains.generation.service import ProgressCallback, make_throttled_progress, user_generation_lock

log = logging.getLogger(__name__)


async def _is_actual_gen(state: FSMContext, gen_id: str) -> bool:
    """Проверяет, актуальна ли генерация в стейте.

    Args:
        state: Состояние FSM пользователя.
        gen_id: ID генерации из контекста.

    Returns:
        True, если актуальна.
    """
    return (await state.get_data()).get("gen_id") == gen_id


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
    """Генерирует аудио и отправляет его в чат.

    Запускается как фоновая задача из handle_title и retry_generation.
    Под локом проверяет и выставляет флаг generating в FSM (защита от
    параллельных запусков) и регистрирует себя в active_tasks, чтобы
    генерацию можно было погасить из cmd_cancel_generation / cmd_logout.
    По ходу дела правит сообщение-статус с прогресс-баром (не чаще
    PROGRESS_EDIT_INTERVAL). При ошибке оставляет prompt и title в FSM
    и вешает кнопку повтора; при отмене убирает сообщение прогресса и
    пробрасывает CancelledError. Успех завершается отправкой аудио и
    очисткой FSM — но только если генерация всё ещё актуальна.

    Args:
        message: Сообщение, от имени которого шлются статусы и аудио.
        state: FSM-контекст текущего пользователя.
        prompt: Подготовленное описание песни.
        title: Название трека.
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
    try:
        if not await _acquire_slot(gen_context=gen_context):
            return

        status = await _start_status(gen_context=gen_context)
        on_progress = _make_progress_reporter(status=status)

        try:
            audio_bytes = await generation_service.run_generation(
                prompt=gen_context.prompt,
                on_progress=on_progress,
            )
        except asyncio.CancelledError:
            await _cleanup_cancelled(gen_context=gen_context, status=status)
            raise
        except Exception as error:
            await _handle_failure(gen_context=gen_context, status=status, error=error)
            return
        await _deliver_result(gen_context=gen_context, status=status, audio_bytes=audio_bytes)
    finally:
        await _release_slot(gen_context=gen_context)


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
        if (await gen_context.state.get_data()).get("generating"):
            await gen_context.message.answer(text="⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
            return False

        gen_context.gen_id = uuid4().hex
        await gen_context.state.update_data(generating=True, gen_id=gen_context.gen_id)
        await register_active_task(uid=gen_context.user_id, task=gen_context.task)
        return True


async def _start_status(gen_context: GenerationContext) -> Message:
    """Показывает «бот отправляет файл…» и создаёт сообщение-статус с прогрессом."""
    await gen_context.message.bot.send_chat_action(
        chat_id=gen_context.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT
    )

    return await gen_context.message.answer(text="🎼 Генерирую… Это может занять до 1–2 минут.")


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
        await gen_context.state.update_data(generating=False)


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
            context=f"Генерация упала (user={gen_context.user_id}, title={gen_context.title!r})",
            err=error,
        )

    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        await gen_context.state.update_data(generating=False)

    with contextlib.suppress(Exception):
        await status.edit_text(
            "😔 Не получилось сгенерировать. Попробуйте ещё раз чуть позже.",
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
    await gen_context.message.answer_audio(audio=file, caption="🎵 Готово!")

    if is_actual:
        await gen_context.state.update_data(generating=False)
        await gen_context.state.set_state(FeedbackStates.waiting_evaluation)
        await gen_context.message.answer(
            text=UIConfig.EVALUATION_PROMPT_TEXT,
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
        log.error("Failed to persist generated title (user=%s)", gen_context.user_id, exc_info=True)


async def _release_slot(gen_context: GenerationContext) -> None:
    """Снимает регистрацию задачи в едином реестре активных генераций.

    Args:
        gen_context: Контекст запуска генерации.
    """
    await unregister_active_task(uid=gen_context.user_id, task=gen_context.task)
