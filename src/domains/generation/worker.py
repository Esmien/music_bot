"""TaskIQ-воркер генерации песни."""

import asyncio
import logging
import uuid
from pathlib import Path

from sqlalchemy import update
from taskiq import Context, TaskiqDepends

from core.broker import generation_broker  # type: ignore[attr-defined]
from core.config import settings  # type: ignore[attr-defined]
from core.database.engine import get_session  # type: ignore[attr-defined]
from core.redis import clear_generation_cancel, is_generation_cancelled  # type: ignore[attr-defined]
from core.utils.error_notify import notify_owner  # type: ignore[attr-defined]
from domains.generation.models import Generation, GenerationStatus  # type: ignore[attr-defined]
from domains.generation.service import run_generation, save_audio_to_storage  # type: ignore[attr-defined]
from shared.contracts.commands import RunGeneration  # type: ignore[attr-defined]
from shared.contracts.events import GenerationFailed, GenerationSucceeded  # type: ignore[attr-defined]
from shared.ports.telegram import TelegramPort  # type: ignore[attr-defined]

log = logging.getLogger(__name__)

broker = generation_broker


async def deliver_generation_audio(
    *,
    telegram: TelegramPort,
    command: RunGeneration,
    audio_path: str,
) -> None:
    """Идемпотентно доставляет сохранённый аудио-файл пользователю в Telegram.

    Сбой доставки не меняет статус генерации на FAILED в БД,
    так как сам артефакт успешно создан и сохранён на диск.

    Args:
        telegram: Порт Telegram для отправки сообщений и аудио.
        command: Исходная команда генерации.
        audio_path: Путь к сохранённому файлу на диске.
    """
    audio_file_path = Path(audio_path)
    if not audio_file_path.exists():
        log.error("Audio file not found for delivery (gen_id=%s, path=%s)", command.gen_id, audio_path)
        delivery_err = FileNotFoundError(f"Audio file not found: {audio_path}")
        await notify_owner(
            telegram_port=telegram,
            context=f"Файл аудио не найден для отправки gen_id={command.gen_id}",
            err=delivery_err,
        )
        failure_event = GenerationFailed(
            user_id=command.user_id,
            chat_id=command.chat_id,
            gen_id=command.gen_id,
            error_message=str(delivery_err),
            stage="delivery",
            status_message_id=command.status_message_id,
        )
        await _publish_event(
            task_name="handle_generation_failed",
            event=failure_event,
            task=handle_generation_failed_task,
        )
        return

    try:
        # Проверка отмены перед доставкой (артефакт уже сохранён, но доставка может быть ненужной)
        if await is_generation_cancelled(gen_id=command.gen_id):
            log.info("Delivery cancelled by user (gen_id=%s)", command.gen_id)
            return

        with audio_file_path.open("rb") as audio_file:
            await telegram.send_audio(
                chat_id=command.chat_id,
                audio=audio_file,
                title=command.title,
                caption="🎵 Готово!",
            )

        if command.status_message_id is not None:
            await telegram.edit_message(
                chat_id=command.chat_id,
                message_id=command.status_message_id,
                text="🎵 Готово!",
            )

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
    except Exception as delivery_error:
        log.exception("Delivery to Telegram failed (gen_id=%s)", command.gen_id)
        await notify_owner(
            telegram_port=telegram,
            context=f"Ошибка отправки аудио в Telegram gen_id={command.gen_id}",
            err=delivery_error,
        )
        failure_event = GenerationFailed(
            user_id=command.user_id,
            chat_id=command.chat_id,
            gen_id=command.gen_id,
            error_message=f"Delivery failed: {type(delivery_error).__name__}: {delivery_error}",
            stage="delivery",
            status_message_id=command.status_message_id,
        )
        await _publish_event(
            task_name="handle_generation_failed",
            event=failure_event,
            task=handle_generation_failed_task,
        )


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
            generation = await session.get(Generation, command.gen_id)
            # Если артефакт уже готов на диске, повторяем доставку без вызова генерации
            if (
                generation is not None
                and generation.status == GenerationStatus.SUCCESS
                and generation.audio_path
                and Path(generation.audio_path).exists()
            ):
                log.info(
                    "Generation %s already has audio artifact at %s, proceeding to delivery",
                    command.gen_id,
                    generation.audio_path,
                )
                await deliver_generation_audio(
                    telegram=telegram,
                    command=command,
                    audio_path=generation.audio_path,
                )
                return

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

        # Сохраняем аудио на диск и записываем метаданные в БД
        audio_path, audio_size, audio_checksum = await save_audio_to_storage(
            audio_bytes=audio_bytes,
            gen_id=command.gen_id,
        )

        async with get_session() as session:
            generation = await session.get(Generation, command.gen_id)
            if generation is None or generation.status is not GenerationStatus.PROCESSING:
                return
            generation.audio_path = audio_path
            generation.audio_size = audio_size
            generation.audio_checksum = audio_checksum
            generation.status = GenerationStatus.SUCCESS
            await session.commit()
    except asyncio.CancelledError:
        async with get_session() as session:
            generation = await session.get(Generation, command.gen_id)
            if generation is not None:
                generation.status = GenerationStatus.CANCELLED
                await session.commit()
        await clear_generation_cancel(gen_id=command.gen_id)

        if command.status_message_id is not None:
            await telegram.send_message(chat_id=command.chat_id, text="Генерация отменена.")
        return
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
        return

    # Отдельный шаг доставки аудио в Telegram
    await deliver_generation_audio(
        telegram=telegram,
        command=command,
        audio_path=audio_path,
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
