"""TaskIQ-воркер генерации песни."""

import asyncio
import logging

from taskiq import Context, TaskiqDepends

from core.broker import generation_broker
from core.config import settings
from core.database.engine import get_session
from core.redis import clear_generation_cancel, is_generation_cancelled
from core.utils.error_notify import notify_owner
from domains.evaluation.worker import request_evaluation_handler
from domains.generation.models import Generation, GenerationStatus
from domains.generation.service import run_generation
from shared.contracts.commands import RunGeneration
from shared.contracts.events import GenerationSucceeded
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
    telegram: TelegramPort = getattr(context, "state", getattr(context, "dependencies", {}))["telegram_port"]

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
            filled = "█" * round(fraction * 10)
            empty = "░" * (10 - round(fraction * 10))
            percent = round(fraction * 100)
            await telegram.edit_message(
                chat_id=command.chat_id,
                message_id=command.status_message_id,
                text=f"🎼 {stage}\n{filled}{empty} {percent}%",
            )

    try:
        audio_bytes = await run_generation(prompt=command.prompt, gen_id=command.gen_id, on_progress=on_progress)

        if await is_generation_cancelled(gen_id=command.gen_id):
            raise asyncio.CancelledError

        await telegram.send_audio(
            chat_id=command.chat_id,
            audio=audio_bytes,
            title=command.title,
            caption="🎵 Готово!",
        )

        succeeded_event = GenerationSucceeded(
            user_id=command.user_id,
            chat_id=command.chat_id,
            gen_id=command.gen_id,
            status_message_id=command.status_message_id,
        )

        await request_evaluation_handler.kiq(succeeded_event)
    except asyncio.CancelledError:
        async with get_session() as session:
            generation = await session.get(Generation, command.gen_id)
            if generation is not None:
                generation.status = GenerationStatus.CANCELLED
                await session.commit()
        await clear_generation_cancel(gen_id=command.gen_id)

        if command.status_message_id is not None:
            await telegram.send_message(chat_id=command.chat_id, text="Генерация отменена.")
    except Exception as error:
        log.exception("Generation failed (gen_id=%s)", command.gen_id)
        await notify_owner(
            telegram_port=telegram,
            context=f"Ошибка генерации gen_id={command.gen_id}",
            err=error,
        )

        await telegram.send_message(
            chat_id=command.chat_id,
            text="😔 Не получилось сгенерировать. Попробуйте ещё раз чуть позже.",
        )
