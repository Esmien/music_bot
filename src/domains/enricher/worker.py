"""Воркер обогащения промптов.

Принимает команду StartEnrichment, вызывает внешний API обогатителя,
валидирует результат и публикует событие EnrichmentCompleted или GenerationFailed.
"""

import logging

from taskiq import Context, TaskiqDepends

from core.broker import enricher_broker  # type: ignore[attr-defined]
from core.utils.error_notify import notify_owner  # type: ignore[attr-defined]
from core.utils.exceptions import EnricherResponseInvalidError  # type: ignore[attr-defined]
from domains.enricher.service import enrich_prompt  # type: ignore[attr-defined]
from domains.enricher.validator import validate_enriched_prompt  # type: ignore[attr-defined]
from shared.contracts.commands import StartEnrichment  # type: ignore[attr-defined]
from shared.contracts.events import EnrichmentCompleted, GenerationFailed  # type: ignore[attr-defined]
from shared.ports.telegram import TelegramPort  # type: ignore[attr-defined]

logger = logging.getLogger(__name__)

broker = enricher_broker


async def _publish_event(*, task_name: str, event: EnrichmentCompleted | GenerationFailed, task: object) -> None:
    """Публикует событие через брокер или зарегистрированную задачу.

    Args:
        task_name: Имя задачи TaskIQ.
        event: Событие для публикации.
        task: Зарегистрированная задача TaskIQ с методом kiq.
    """
    broker_kicker = getattr(broker, "kicker", None)
    if callable(broker_kicker):
        await broker_kicker(task_name=task_name).kiq(event)
        return

    task_kiq = getattr(task, "kiq", None)
    if not callable(task_kiq):
        raise TypeError(f"Task {task_name!r} does not expose kiq")

    await task_kiq(event)


@enricher_broker.task(task_name="enrich_prompt")
async def enrich_prompt_task(
    command: StartEnrichment | dict | str,
    context: Context = TaskiqDepends(),
) -> None:
    """Обогащает промпт пользователя через внешний API.

    Args:
        command: Команда с user_id, chat_id, prompt и историей диалога.
        context: Контекст TaskIQ с зависимостями (telegram_port).
    """
    if isinstance(command, str):
        command = StartEnrichment.model_validate_json(command)
    elif not isinstance(command, StartEnrichment):
        command = StartEnrichment.model_validate(command)

    telegram_port: TelegramPort | None = None
    state = getattr(context, "state", None) or getattr(context, "dependencies", None) or {}
    get_state = getattr(state, "get", None)
    if callable(get_state):
        telegram_port = get_state("telegram_port")

    user_id = command.user_id
    chat_id = command.chat_id

    try:
        logger.info(f"Starting enrichment for user_id={user_id}, prompt length={len(command.prompt)}")

        # Вызов внешнего API обогатителя
        raw_response = await enrich_prompt(prompt=command.prompt, history=command.history)

        if raw_response is None:
            raise EnricherResponseInvalidError("Enricher returned None")

        # Валидация и парсинг ответа
        enriched_prompt = validate_enriched_prompt(raw=raw_response)

        logger.info(f"Enrichment completed for user_id={user_id}, enriched length={len(enriched_prompt)}")

        # Публикуем событие успеха
        event = EnrichmentCompleted(
            user_id=user_id,
            chat_id=chat_id,
            initial_prompt=command.prompt,
            enriched_prompt=enriched_prompt,
            status_message_id=command.status_message_id,
        )
        await _publish_event(
            task_name="handle_enrichment_completed",
            event=event,
            task=handle_enrichment_completed_task,
        )

    except Exception as exc:
        logger.error(f"Enrichment failed for user_id={user_id}: {exc}", exc_info=True)

        # Уведомляем владельца
        await notify_owner(
            telegram_port=telegram_port,
            err=exc,
            context=f"Ошибка обогащения промпта для user_id={user_id}",
        )

        # Публикуем событие сбоя
        failure_event = GenerationFailed(
            user_id=user_id,
            chat_id=chat_id,
            gen_id=None,
            error_message=f"Не удалось обогатить промпт: {type(exc).__name__}",
            stage="enrichment",
            status_message_id=command.status_message_id,
        )
        await _publish_event(
            task_name="handle_generation_failed",
            event=failure_event,
            task=handle_generation_failed_task,
        )


@enricher_broker.task(task_name="handle_enrichment_completed")
async def handle_enrichment_completed_task(
    event: EnrichmentCompleted | dict | str,
    context: Context = TaskiqDepends(),
) -> None:
    """Передаёт событие успешного обогащения обработчику бота.

    Args:
        event: Событие с результатом обогащения.
        context: Контекст TaskIQ с зависимостями.
    """
    if isinstance(event, str):
        event = EnrichmentCompleted.model_validate_json(event)
    elif not isinstance(event, EnrichmentCompleted):
        event = EnrichmentCompleted.model_validate(event)

    from domains.enricher.handlers import handle_enrichment_completed_event  # type: ignore[attr-defined]

    await handle_enrichment_completed_event(event=event, context=context)


@enricher_broker.task(task_name="handle_generation_failed")
async def handle_generation_failed_task(
    event: GenerationFailed | dict | str,
    context: Context = TaskiqDepends(),
) -> None:
    """Передаёт событие сбоя обработчику бота.

    Args:
        event: Событие с описанием ошибки.
        context: Контекст TaskIQ с зависимостями.
    """
    if isinstance(event, str):
        event = GenerationFailed.model_validate_json(event)
    elif not isinstance(event, GenerationFailed):
        event = GenerationFailed.model_validate(event)

    from domains.enricher.handlers import handle_generation_failed_event  # type: ignore[attr-defined]

    await handle_generation_failed_event(event=event, context=context)
