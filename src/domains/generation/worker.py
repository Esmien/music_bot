"""TaskIQ-воркер генерации песни."""

import asyncio
import logging
import tempfile
from pathlib import Path

from taskiq import Context, TaskiqDepends

from core.broker import generation_broker
from core.config import settings
from core.database.engine import get_session
from core.redis import clear_generation_cancel, is_generation_cancelled
from core.utils.error_notify import notify_owner
from domains.generation.models import Generation, GenerationStatus
from domains.generation.service import run_generation
from shared.contracts.commands import RunGeneration
from shared.contracts.events import GenerationFailed, GenerationSucceeded
from shared.ports.telegram import TelegramPort

log = logging.getLogger(__name__)


@generation_broker.task(
    task_name="run_generation",
    queue_name=settings.rabbitmq.queue_name("generation"),
)
async def run_generation_task(
    command: RunGeneration,
    context: Context = TaskiqDepends(),
) -> None:
    """Запускает генерацию и публикует событие результата.

    Args:
        command: Команда генерации.
        context: Контекст TaskIQ с TelegramPort.
    """
    telegram: TelegramPort = context.dependencies["telegram_port"]

    async with get_session() as session:
        generation = await session.get(Generation, command.gen_id)
        if generation is None:
            return
        if generation.status is not GenerationStatus.PENDING:
            log.info("Generation %s is already processed", command.gen_id)
            return

    async def on_progress(stage: str, fraction: float) -> None:
        if await is_generation_cancelled(gen_id=command.gen_id):
            raise asyncio.CancelledError
        if command.status_message_id is not None:
            await telegram.edit_message(
                chat_id=command.chat_id,
                message_id=command.status_message_id,
                text=f"🎼 {stage}\n{'█' * round(fraction * 10)}{'░' * (10 - round(fraction * 10))} {round(fraction * 100)}%",
            )

    try:
        audio_bytes = await run_generation(prompt=command.prompt, on_progress=on_progress)

        if await is_generation_cancelled(gen_id=command.gen_id):
            raise asyncio.CancelledError

        with tempfile.NamedTemporaryFile(prefix=f"generation-{command.gen_id}-", suffix=".mp3", delete=False) as audio_file:
            audio_file.write(audio_bytes)
            audio_path = Path(audio_file.name)

        await generation_broker.kicker(task_name="handle_generation_succeeded").kiq(
            GenerationSucceeded(
                user_id=command.user_id,
                chat_id=command.chat_id,
                gen_id=command.gen_id,
                title=command.title,
                audio_file_path=str(audio_path),
                status_message_id=command.status_message_id,
            )
        )
    except asyncio.CancelledError:
        async with get_session() as session:
            generation = await session.get(Generation, command.gen_id)
            if generation is not None:
                generation.status = GenerationStatus.CANCELLED
                await session.commit()
        await clear_generation_cancel(gen_id=command.gen_id)
        await generation_broker.kicker(task_name="handle_generation_failed").kiq(
            GenerationFailed(
                user_id=command.user_id,
                chat_id=command.chat_id,
                gen_id=command.gen_id,
                error_message="Generation was cancelled",
                stage="cancelled",
                status_message_id=command.status_message_id,
            )
        )
    except Exception as error:
        log.exception("Generation failed (gen_id=%s)", command.gen_id)
        await notify_owner(
            telegram_port=telegram,
            context=f"Ошибка генерации gen_id={command.gen_id}",
            err=error,
        )
        await generation_broker.kicker(task_name="handle_generation_failed").kiq(
            GenerationFailed(
                user_id=command.user_id,
                chat_id=command.chat_id,
                gen_id=command.gen_id,
                error_message=type(error).__name__,
                stage="generation",
                status_message_id=command.status_message_id,
            )
        )


@generation_broker.task(task_name="handle_generation_succeeded")
async def handle_generation_succeeded(
    event: GenerationSucceeded,
    context: Context = TaskiqDepends(),
) -> None:
    """Отправляет результат генерации пользователю.

    Args:
        event: Событие успешной генерации.
        context: Контекст TaskIQ с TelegramPort.
    """
    telegram: TelegramPort = context.dependencies["telegram_port"]

    async with get_session() as session:
        generation = await session.get(Generation, event.gen_id)
        if generation is None or generation.status is not GenerationStatus.PENDING:
            return
        generation.status = GenerationStatus.SUCCESS
        await session.commit()

    await telegram.send_audio(
        chat_id=event.chat_id,
        audio=event.audio_file_path,
        title=event.title,
        caption="🎵 Готово!",
    )

    if event.status_message_id is not None:
        await telegram.edit_message(
            chat_id=event.chat_id,
            message_id=event.status_message_id,
            text="🎵 Готово!",
        )


@generation_broker.task(task_name="handle_generation_failed")
async def handle_generation_failed(
    event: GenerationFailed,
    context: Context = TaskiqDepends(),
) -> None:
    """Показывает пользователю ошибку генерации.

    Args:
        event: Событие сбоя генерации.
        context: Контекст TaskIQ с TelegramPort.
    """
    telegram: TelegramPort = context.dependencies["telegram_port"]

    if event.stage == "cancelled":
        await telegram.send_message(chat_id=event.chat_id, text="Генерация отменена.")
        return

    await telegram.send_message(
        chat_id=event.chat_id,
        text=event.user_error_message or "😔 Не получилось сгенерировать. Попробуйте ещё раз чуть позже.",
    )
