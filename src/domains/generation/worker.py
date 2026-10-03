"""TaskIQ-воркер генерации песни."""

import asyncio
import io
import logging
import time
import uuid
from pathlib import Path

from sqlalchemy import update
from taskiq import Context, TaskiqDepends

from core.broker import generation_broker  # type: ignore[attr-defined]
from core.config import settings  # type: ignore[attr-defined]
from core.database.engine import get_session  # type: ignore[attr-defined]
from core.metrics import (  # type: ignore[attr-defined]
    DELIVERY_LATENCY_SECONDS,
    DELIVERY_TOTAL,
    GENERATION_LATENCY_SECONDS,
    GENERATION_TOTAL,
    STALE_ATTEMPTS_TOTAL,
    STORAGE_SAVE_LATENCY_SECONDS,
)
from core.redis import (  # type: ignore[attr-defined]
    clear_delivery_cancel,
    clear_generation_cancel,
    is_delivery_cancelled,
    is_generation_cancelled,
)
from core.utils.error_notify import notify_owner  # type: ignore[attr-defined]
from domains.generation.models import Generation, GenerationStatus  # type: ignore[attr-defined]
from domains.generation.service import (  # type: ignore[attr-defined]
    claim_delivery_atomic,
    run_generation,
    save_audio_to_storage,
)
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
    delivery_attempt_id: str,
) -> None:
    """Идемпотентно доставляет сохранённый аудио-файл пользователю в Telegram.

    Сбой доставки не меняет статус генерации на FAILED в БД,
    так как сам артефакт успешно создан и сохранён на диск.

    Args:
        telegram: Порт Telegram для отправки сообщений и аудио.
        command: Исходная команда генерации.
        audio_path: Путь к сохранённому файлу на диске.
        delivery_attempt_id: Идентификатор текущей попытки доставки.
    """
    # Проверка delivery cancel-токена (статус генерации уже SUCCESS, доставка пропускается)
    if await is_delivery_cancelled(gen_id=command.gen_id):
        log.info("Delivery cancelled by user (gen_id=%s, status=SUCCESS preserved)", command.gen_id)
        DELIVERY_TOTAL.labels(status="skipped").inc()
        async with get_session() as session:
            from domains.generation.models import DeliveryStatus

            await session.execute(
                update(Generation)
                .where(
                    Generation.id == command.gen_id,
                    Generation.delivery_attempt_id == delivery_attempt_id,
                )
                .values(delivery_status=DeliveryStatus.FAILED)
            )
            await session.commit()
        await clear_delivery_cancel(gen_id=command.gen_id)
        return

    async with get_session() as session:
        generation = await session.get(Generation, command.gen_id)
        if generation is None:
            log.error("Generation record not found for delivery (gen_id=%s)", command.gen_id)
            return

        expected_size = generation.audio_size
        expected_checksum = generation.audio_checksum

    from domains.generation.service import verify_audio_integrity

    is_valid, error_message = verify_audio_integrity(
        audio_path=audio_path,
        expected_size=expected_size,
        expected_checksum=expected_checksum,
    )

    if not is_valid:
        log.error(
            "Audio integrity check failed for delivery (gen_id=%s, path=%s): %s",
            command.gen_id,
            audio_path,
            error_message,
        )
        DELIVERY_TOTAL.labels(status="failed").inc()
        delivery_err = ValueError(f"Audio integrity check failed: {error_message}")
        try:
            await notify_owner(
                telegram_port=telegram,
                context=f"Проверка целостности аудио не прошла для gen_id={command.gen_id}",
                err=delivery_err,
            )
        except Exception:
            log.exception("Failed to notify owner about audio integrity failure (gen_id=%s)", command.gen_id)
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

    delivery_start = time.monotonic()
    try:
        audio_bytes = await asyncio.to_thread(Path(audio_path).read_bytes)
        await telegram.send_audio(
            chat_id=command.chat_id,
            audio=io.BytesIO(audio_bytes),
            title=command.title,
            caption="🎵 Готово!",
        )

        if command.status_message_id is not None:
            await telegram.edit_message(
                chat_id=command.chat_id,
                message_id=command.status_message_id,
                text="🎵 Готово!",
            )

        async with get_session() as session:
            from domains.generation.models import DeliveryStatus

            await session.execute(
                update(Generation)
                .where(
                    Generation.id == command.gen_id,
                    Generation.delivery_attempt_id == delivery_attempt_id,
                )
                .values(delivery_status=DeliveryStatus.DELIVERED)
            )
            await session.commit()

        DELIVERY_LATENCY_SECONDS.observe(time.monotonic() - delivery_start)
        DELIVERY_TOTAL.labels(status="success").inc()

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
        DELIVERY_LATENCY_SECONDS.observe(time.monotonic() - delivery_start)
        DELIVERY_TOTAL.labels(status="failed").inc()
        async with get_session() as session:
            from domains.generation.models import DeliveryStatus

            await session.execute(
                update(Generation)
                .where(
                    Generation.id == command.gen_id,
                    Generation.delivery_attempt_id == delivery_attempt_id,
                )
                .values(delivery_status=DeliveryStatus.FAILED)
            )
            await session.commit()
        try:
            await notify_owner(
                telegram_port=telegram,
                context=f"Ошибка отправки аудио в Telegram gen_id={command.gen_id}",
                err=delivery_error,
            )
        except Exception:
            log.exception("Failed to notify owner about delivery error (gen_id=%s)", command.gen_id)
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


async def _claim_generation(
    *,
    command: RunGeneration,
    attempt_id: str,
    telegram: TelegramPort,
) -> bool:
    """Атомарно переводит генерацию в статус PROCESSING или доставляет готовый артефакт.

    Args:
        command: Команда запуска генерации.
        attempt_id: Идентификатор текущей попытки.
        telegram: Порт Telegram для доставки сообщений и аудио.

    Returns:
        True, если генерация успешно зарезервирована для выполнения, иначе False.
    """
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

        if claimed_id is not None:
            return True

        generation = await session.get(Generation, command.gen_id)
        if (
            generation is not None
            and generation.status == GenerationStatus.SUCCESS
            and generation.audio_path
            and Path(generation.audio_path).exists()
        ):
            delivery_attempt_id = str(uuid.uuid4())
            delivery_claimed = await claim_delivery_atomic(
                gen_id=command.gen_id,
                delivery_attempt_id=delivery_attempt_id,
            )
            if delivery_claimed:
                log.info(
                    "Generation %s already has audio artifact at %s, proceeding to delivery (delivery_attempt=%s)",
                    command.gen_id,
                    generation.audio_path,
                    delivery_attempt_id,
                )
                await deliver_generation_audio(
                    telegram=telegram,
                    command=command,
                    audio_path=generation.audio_path,
                    delivery_attempt_id=delivery_attempt_id,
                )
            else:
                log.info(
                    "Delivery for generation %s already claimed or completed in _claim_generation, skipping",
                    command.gen_id,
                )
                DELIVERY_TOTAL.labels(status="skipped").inc()
            return False

        log.info("Generation %s already claimed or processed, skipping", command.gen_id)
        STALE_ATTEMPTS_TOTAL.inc()
        return False


async def _save_generation_success(
    *,
    gen_id: int,
    attempt_id: str,
    audio_path: str,
    audio_size: int,
    audio_checksum: str,
) -> bool:
    """Сохраняет метаданные успешной генерации в БД.

    Args:
        gen_id: Идентификатор генерации.
        attempt_id: Идентификатор попытки.
        audio_path: Путь к сохранённому файлу на диске.
        audio_size: Размер аудио в байтах.
        audio_checksum: Контрольная сумма файла.

    Returns:
        True, если запись обновлена успешно, False при конкурентной модификации.
    """
    async with get_session() as session:
        success_stmt = (
            update(Generation)
            .where(
                Generation.id == gen_id,
                Generation.attempt_id == attempt_id,
                Generation.status == GenerationStatus.PROCESSING,
            )
            .values(
                audio_path=audio_path,
                audio_size=audio_size,
                audio_checksum=audio_checksum,
                status=GenerationStatus.SUCCESS,
            )
            .returning(Generation.id)
        )
        result = await session.execute(success_stmt)
        updated_id = result.scalar_one_or_none()
        await session.commit()
        return updated_id is not None


async def _handle_generation_cancel(
    *,
    command: RunGeneration,
    attempt_id: str,
    telegram: TelegramPort,
) -> None:
    """Обрабатывает отмену генерации пользователем.

    Args:
        command: Команда генерации.
        attempt_id: Идентификатор попытки.
        telegram: Порт Telegram для отправки уведомления.
    """
    async with get_session() as session:
        cancel_stmt = (
            update(Generation)
            .where(
                Generation.id == command.gen_id,
                Generation.attempt_id == attempt_id,
            )
            .values(status=GenerationStatus.CANCELLED)
            .returning(Generation.id)
        )
        await session.execute(cancel_stmt)
        await session.commit()
    GENERATION_TOTAL.labels(status="cancelled").inc()
    # Очищаем generation cancel-token после обработки отмены
    await clear_generation_cancel(gen_id=command.gen_id)

    if command.status_message_id is not None:
        await telegram.send_message(chat_id=command.chat_id, text="Генерация отменена.")


async def _handle_generation_failure(
    *,
    command: RunGeneration,
    attempt_id: str,
    telegram: TelegramPort,
    error: Exception,
) -> None:
    """Обрабатывает сбой выполнения генерации и публикует событие ошибки.

    Args:
        command: Команда генерации.
        attempt_id: Идентификатор попытки.
        telegram: Порт Telegram для уведомления владельца.
        error: Возникшее исключение.
    """
    log.exception("Generation failed (gen_id=%s)", command.gen_id)
    try:
        async with get_session() as session:
            fail_stmt = (
                update(Generation)
                .where(
                    Generation.id == command.gen_id,
                    Generation.attempt_id == attempt_id,
                )
                .values(status=GenerationStatus.FAILED)
                .returning(Generation.id)
            )
            result = await session.execute(fail_stmt)
            if result.scalar_one_or_none() is None:
                STALE_ATTEMPTS_TOTAL.inc()
            await session.commit()
    except Exception as db_error:
        log.exception(
            "Failed to update generation %s status to FAILED in DB: %s",
            command.gen_id,
            db_error,
        )

    try:
        await notify_owner(
            telegram_port=telegram,
            context=f"Ошибка генерации gen_id={command.gen_id}",
            err=error,
        )
    except Exception:
        log.exception(
            "Failed to notify owner about generation failure (gen_id=%s)",
            command.gen_id,
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
    GENERATION_TOTAL.labels(status="failed").inc()


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
    claimed = await _claim_generation(
        command=command,
        attempt_id=attempt_id,
        telegram=telegram,
    )
    if not claimed:
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
        gen_start = time.monotonic()
        audio_bytes = await run_generation(prompt=command.prompt, gen_id=command.gen_id, on_progress=on_progress)
        GENERATION_LATENCY_SECONDS.observe(time.monotonic() - gen_start)

        if await is_generation_cancelled(gen_id=command.gen_id):
            raise asyncio.CancelledError

        save_start = time.monotonic()
        audio_path, audio_size, audio_checksum = save_audio_to_storage(
            audio_bytes=audio_bytes,
            gen_id=command.gen_id,
        )
        STORAGE_SAVE_LATENCY_SECONDS.observe(time.monotonic() - save_start)

        is_saved = await _save_generation_success(
            gen_id=command.gen_id,
            attempt_id=attempt_id,
            audio_path=audio_path,
            audio_size=audio_size,
            audio_checksum=audio_checksum,
        )
        if is_saved:
            GENERATION_TOTAL.labels(status="success").inc()
        else:
            log.warning(
                "Generation %s was modified concurrently (attempt %s), skipping delivery",
                command.gen_id,
                attempt_id,
            )
            STALE_ATTEMPTS_TOTAL.inc()
            return
    except asyncio.CancelledError:
        await _handle_generation_cancel(
            command=command,
            attempt_id=attempt_id,
            telegram=telegram,
        )
        return
    except Exception as error:
        await _handle_generation_failure(
            command=command,
            attempt_id=attempt_id,
            telegram=telegram,
            error=error,
        )
        return

    delivery_attempt_id = str(uuid.uuid4())
    delivery_claimed = await claim_delivery_atomic(
        gen_id=command.gen_id,
        delivery_attempt_id=delivery_attempt_id,
    )
    if delivery_claimed:
        await deliver_generation_audio(
            telegram=telegram,
            command=command,
            audio_path=audio_path,
            delivery_attempt_id=delivery_attempt_id,
        )
    else:
        log.info(
            "Delivery for generation %s already claimed or completed, skipping (attempt=%s)",
            command.gen_id,
            attempt_id,
        )
        DELIVERY_TOTAL.labels(status="skipped").inc()


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

    from domains.generation.handlers import handle_generation_failed_event

    await handle_generation_failed_event(event=event, context=context)
