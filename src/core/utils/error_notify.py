"""Логирование ошибок и уведомление владельца бота.

Модуль обеспечивает централизованную отправку traceback владельцу через Telegram."""

import html
import logging
import traceback
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.ports.telegram import TelegramPort

from aiogram import Bot

from core.config import settings

log = logging.getLogger(__name__)


async def notify_owner(
    *,
    bot: Bot | None = None,
    context: str = "",
    err: Exception | None = None,
    telegram_port: "TelegramPort | None" = None,
) -> None:
    """Логирует ошибку и отправляет traceback владельцу бота в Telegram.

    Args:
        bot: Экземпляр бота (может быть None для служебных апдейтов).
        context: Краткое описание, где произошла ошибка.
        err: Пойманное исключение.
        telegram_port: Порт Telegram для отправки из воркеров (опционально).

    Returns:
        None.
    """
    if err is None:
        log.warning("notify_owner called without error")
        return
    log.error(context, exc_info=err)

    # Позиционно: первый аргумент format_exception — positional-only
    traceback_text = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    # Обрезаем с начала: конец стека (где возникла ошибка) важнее первых кадров
    if len(traceback_text) > 3000:
        traceback_text = "…\n" + traceback_text[-2997:]

    try:
        if telegram_port:
            await telegram_port.notify_owner(message=context, context={"traceback": traceback_text})
        elif bot and settings.bot.BOT_OWNER_ID:
            text = f"🐞 <b>{html.escape(context)}</b>\n<code>{html.escape(traceback_text)}</code>"
            await bot.send_message(chat_id=settings.bot.BOT_OWNER_ID, text=text, parse_mode="HTML")
        elif not settings.bot.BOT_OWNER_ID:
            # BOT_OWNER_ID == 0 означает, что владелец не настроен — ошибка уже записана в лог
            return
        else:
            log.warning("notify_owner called without bot or telegram_port")
    except Exception:
        log.exception("Failed to notify owner")
