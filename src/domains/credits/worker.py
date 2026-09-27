"""Воркер для обработки команд домена credits."""

import logging

import httpx
from taskiq import Context, TaskiqDepends

from core.broker import broker
from core.config import settings
from domains.credits.credits_messages import (
    API_KEY_NOT_CONFIGURED_CONTEXT,
    API_KEY_NOT_SET_ERROR,
    CREDITS_API_ERROR,
    CREDITS_API_KEY_NOT_CONFIGURED,
    CREDITS_CHECK_FAILED,
    CREDITS_CHECK_FAILED_LOG,
    CREDITS_CHECK_FAILED_OWNER_CONTEXT,
    CREDITS_SUMMARY,
)
from domains.credits.service import get_credits_summary
from shared.contracts.commands import GetCredits
from shared.ports.telegram import TelegramPort

log = logging.getLogger(__name__)


@broker.task(task_name="credits.get_credits", queue_name=settings.rabbitmq.queue_name("credits"))
async def handle_get_credits(
    command: GetCredits,
    context: Context = TaskiqDepends(),
) -> None:
    """Обрабатывает запрос на проверку кредитов OpenRouter.

    Args:
        command: Команда GetCredits с chat_id пользователя.
        context: Контекст TaskIQ с доступом к порту Telegram.
    """
    telegram: TelegramPort = context.state["telegram_port"]

    api_key = settings.bot.OPENROUTER_API_KEY
    if not api_key:
        await telegram.notify_owner(
            message=API_KEY_NOT_CONFIGURED_CONTEXT,
            context={"error": API_KEY_NOT_SET_ERROR},
        )
        await telegram.send_message(chat_id=command.chat_id, text=CREDITS_API_KEY_NOT_CONFIGURED)
        return

    try:
        summary = await get_credits_summary(
            api_key=api_key,
            song_price=settings.generation.SONG_PRICE,
        )
        if summary.status_code != 200:
            await telegram.send_message(
                chat_id=command.chat_id,
                text=CREDITS_API_ERROR.format(status_code=summary.status_code),
            )
            return

        await telegram.send_message(
            chat_id=command.chat_id,
            text=CREDITS_SUMMARY.format(
                total_songs=summary.total_songs,
                used_songs=summary.used_songs,
                remaining_songs=summary.remaining_songs,
            ),
        )
    except httpx.HTTPError as error:
        log.error(CREDITS_CHECK_FAILED_LOG, command.chat_id)
        await telegram.notify_owner(
            message=CREDITS_CHECK_FAILED_OWNER_CONTEXT.format(user_id=command.chat_id),
            context={"error": str(error), "chat_id": command.chat_id},
        )
        await telegram.send_message(chat_id=command.chat_id, text=CREDITS_CHECK_FAILED)
