"""Воркер обогащения промптов.

Принимает команду StartEnrichment, вызывает внешний API обогатителя,
валидирует результат и публикует событие EnrichmentCompleted или GenerationFailed.
"""

import logging

from taskiq import Context, TaskiqDepends

from core.broker import broker
from core.utils.error_notify import notify_owner
from core.utils.exceptions import EnricherResponseInvalidError
from domains.enricher.service import enrich_prompt
from domains.enricher.validator import validate_enriched_prompt
from shared.contracts.commands import StartEnrichment
from shared.contracts.events import EnrichmentCompleted, GenerationFailed
from shared.ports.telegram import TelegramPort

logger = logging.getLogger(__name__)


@broker.task(task_name="enrich_prompt")
async def enrich_prompt_task(
    command: StartEnrichment,
    context: Context = TaskiqDepends(),
) -> None:
    """Обогащает промпт пользователя через внешний API.

    Args:
        command: Команда с user_id, chat_id, prompt и историей диалога.
        context: Контекст TaskIQ с зависимостями (telegram_port).
    """
    telegram_port: TelegramPort = context.dependencies["telegram_port"]
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
        await broker.kicker(task_name="handle_enrichment_completed").kiq(event)

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
        await broker.kicker(task_name="handle_generation_failed").kiq(failure_event)
