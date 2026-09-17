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

    # BOT_OWNER_ID == 0 означает, что владелец не настроен — шлём только в лог
    if bot is None or not config.BOT_OWNER_ID:
        return

    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    # Обрезаем с начала: конец стека (где возникла ошибка) важнее первых кадров
    if len(tb) > 3000:
        tb = "…\n" + tb[-2997:]
    text = f"🐞 <b>{html.escape(context)}</b>\n<code>{html.escape(tb)}</code>"

    try:
        await bot.send_message(config.BOT_OWNER_ID, text, parse_mode="HTML")
    except Exception:
        log.exception("Failed to notify owner")
