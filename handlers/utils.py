"""Вспомогательные функции."""

import html
import logging
import traceback

import config

log = logging.getLogger(__name__)


async def notify_owner(bot, context: str, err: Exception) -> None:
    """Логирует ошибку и отправляет traceback владельцу бота в Telegram.

    Args:
        bot: Экземпляр бота (может быть None для служебных апдейтов).
        context: Краткое описание, где произошла ошибка.
        err: Пойманное исключение.
    """
    log.exception(context)

    if bot is None or not config.BOT_OWNER_ID:
        return

    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    text = f"🐞 <b>{html.escape(context)}</b>\n<code>{html.escape(tb[-3000:])}</code>"

    try:
        await bot.send_message(config.BOT_OWNER_ID, text, parse_mode="HTML")
    except Exception:
        log.exception("Failed to notify owner")
