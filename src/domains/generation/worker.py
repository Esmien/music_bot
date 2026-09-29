"""TaskIQ-воркер генерации песни."""

import asyncio
import logging
import uuid

from sqlalchemy import update
from taskiq import Context, TaskiqDepends

from core.broker import generation_broker  # type: ignore[attr-defined]
from core.config import settings  # type: ignore[attr-defined]
from core.database.engine import get_session  # type: ignore[attr-defined]
from core.redis import clear_generation_cancel, is_generation_cancelled  # type: ignore[attr-defined]
from core.utils.error_notify import notify_owner  # type: ignore[attr-defined]
from domains.generation.models import Generation, GenerationStatus  # type: ignore[attr-defined]
from domains.generation.service import run_generation  # type: ignore[attr-defined]
from shared.contracts.commands import RunGeneration  # type: ignore[attr-defined]
from shared.contracts.events import GenerationFailed, GenerationSucceeded  # type: ignore[attr-defined]
from shared.ports.telegram import TelegramPort  # type: ignore[attr-defined]

log = logging.getLogger(__name__)

broker = generation_broker


async def _publish_event(*, task_name: str, event: GenerationSucceeded | GenerationFailed, task: object) -> None:
    """Публикует событие через брокер или зарегистрированную задачу.

    Args:
        task_name: Имя задачи TaskIQ.
        event: Событие для публикации.
        task: Зарегистрированная задача TaskIQ с методом kiq.

    Raises:
        TypeError: Если задача не предоставляет метод kiq.
    """
    broker_kicker = getattr(broker, "kicker", None)
    if callable(broker_kicker):
        await broker_kicker(task_name=task_name).kiq(event)
        return

    task_kiq = getattr(task, "kiq", None)
    if not callable(task_kiq):
        raise TypeError(f"Task {task_name!r} does not expose kiq")

    await task_kiq(event)


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
    state_dict = getattr(context, "state", getattr(context, "dependencies", {}))
    telegram: TelegramPort = state_dict["telegram_port"]

    attempt_id = str(uuid.uuid4())
    async with get_session() as session:
        claim_stmt = (
            update(Generation)
            .where(
                Generation.id == command.gen_id,
                Generation.status == GenerationStatus.PENDING,
            )
            .values(status=GenerationStatus.PROCESSING, attempt_id=attempt_id)
            .returning(Generation.id)
        )
        result = await session.execute(claim_stmt)
        claimed_id = result.scalar_one_or_none()
        await session.commit()

        if claimed_id is None:
            log.info("Generation %s already claimed or processed, skipping", command.gen_id)
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

        # Обновляем статус генерации в БД
        async with get_session() as session:
            generation = await session.get(Generation, command.gen_id)
            if generation is None or generation.status is not GenerationStatus.PROCESSING:
                return
            generation.status = GenerationStatus.SUCCESS
            await session.commit()

        # Редактируем статусное сообщение
        if command.status_message_id is not None:
            await telegram.edit_message(
                chat_id=command.chat_id,
                message_id=command.status_message_id,
                text="🎵 Готово!",
            )

        # Публикуем событие успеха генерации
        succeeded_event = GenerationSucceeded(
            user_id=command.user_id,
            chat_id=command.chat_id,
            gen_id=command.gen_id,
            status_message_id=command.status_message_id,
        )
        await _publish_event(
            task_name="handle_generation_succeeded",
            event=succeeded_event,
            task=handle_generation_succeeded_task,
        )
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
        async with get_session() as session:
            generation = await session.get(Generation, command.gen_id)
            if generation is not None:
                generation.status = GenerationStatus.FAILED
                await session.commit()

        await notify_owner(
            telegram_port=telegram,
            context=f"Ошибка генерации gen_id={command.gen_id}",
            err=error,
        )

        failure_event = GenerationFailed(
            user_id=command.user_id,
            chat_id=command.chat_id,
            gen_id=command.gen_id,
            error_message=f"Generation failed: {type(error).__name__}: {error}",
            stage="generation",
            status_message_id=command.status_message_id,
        )
        await _publish_event(
            task_name="handle_generation_failed",
            event=failure_event,
            task=handle_generation_failed_task,
        )


@generation_broker.task(task_name="handle_generation_succeeded")
async def handle_generation_succeeded_task(
    event: GenerationSucceeded | dict | str,
    context: Context = TaskiqDepends(),
) -> None:
    """Передаёт событие успешной генерации обработчику бота.

    Args:
        event: Событие с результатом генерации.
        context: Контекст TaskIQ с зависимостями.
    """
    if isinstance(event, str):
        event = GenerationSucceeded.model_validate_json(event)
    elif not isinstance(event, GenerationSucceeded):
        event = GenerationSucceeded.model_validate(event)

    from domains.evaluation.handlers import handle_generation_succeeded_event

    await handle_generation_succeeded_event(event=event, context=context)


@generation_broker.task(task_name="handle_generation_failed")
async def handle_generation_failed_task(
    event: GenerationFailed | dict | str,
    context: Context = TaskiqDepends(),
) -> None:
    """Передаёт событие сбоя генерации обработчику бота.

    Args:
        event: Событие с описанием ошибки.
        context: Контекст TaskIQ с зависимостями.
    """
    if isinstance(event, str):
        event = GenerationFailed.model_validate_json(event)
    elif not isinstance(event, GenerationFailed):
        event = GenerationFailed.model_validate(event)

    from domains.enricher.handlers import handle_generation_failed_event

    await handle_generation_failed_event(event=event, context=context)
