"""Централизованное управление жизненным циклом ресурсов приложения.

Обеспечивает корректное закрытие всех соединений (Redis, БД, Bot session)
при остановке бота через SIGTERM/SIGINT или при ошибке запуска.
"""

import logging
from typing import Protocol

from core.database.engine import shutdown_db
from core.redis import shutdown_redis

log = logging.getLogger(__name__)


class BotSessionLike(Protocol):
    """Протокол для сессии бота с методом close."""

    async def close(self) -> None:
        """Закрытие HTTP-сессии."""
        ...


class BotLike(Protocol):
    """Протокол для объектов с HTTP-сессией, совместимых с aiogram.Bot."""

    @property
    def session(self) -> BotSessionLike:
        """HTTP-сессия бота с методом close()."""
        ...


async def shutdown_all(bot: BotLike | None = None) -> None:
    """Закрывает все ресурсы приложения в правильном порядке.

    Args:
        bot: Экземпляр бота для закрытия HTTP-сессии. Может быть None
            если бот ещё не был создан (например, ошибка при старте).
    """
    log.info("Starting graceful shutdown")

    # Чистим реестр активных задач текущего экземпляра перед shutdown
    try:
        from domains.generation.registries.task_registry import clear_active_tasks

        await clear_active_tasks()
        log.info("Active tasks registry cleaned")
    except Exception as e:
        log.exception("Error cleaning active tasks: %s", e)

    # Снимаем регистрацию экземпляра перед закрытием других ресурсов
    try:
        from core.instance import current_instance

        await current_instance.unregister()
        log.info("Bot instance unregistered")
    except Exception as e:
        log.exception("Error unregistering bot instance: %s", e)

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
