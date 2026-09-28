"""Централизованное управление жизненным циклом ресурсов приложения.

Обеспечивает корректное закрытие всех соединений (Redis, БД, Bot session)
при остановке бота через SIGTERM/SIGINT или при ошибке запуска.
"""

import logging

from aiogram import Bot

from core.database.engine import shutdown_db
from core.redis import shutdown_redis

log = logging.getLogger(__name__)


async def shutdown_all(bot: Bot | None = None) -> None:
    """Закрывает все ресурсы приложения в правильном порядке.

    Args:
        bot: Экземпляр бота для закрытия HTTP-сессии. Может быть None,
             если бот ещё не был создан (например, ошибка при старте).
    """
    log.info("Starting graceful shutdown")

    if bot:
        try:
            await bot.session.close()
            log.info("Bot session closed")
        except Exception as e:
            log.exception("Error closing bot session: %s", e)

    try:
        await shutdown_redis()
        log.info("Redis connection closed")
    except Exception as e:
        log.exception("Error closing Redis: %s", e)

    try:
        await shutdown_db()
        log.info("Database engine closed")
    except Exception as e:
        log.exception("Error closing database: %s", e)

    log.info("Graceful shutdown completed")
