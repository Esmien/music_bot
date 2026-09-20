"""Точка входа Telegram-бота: настройка Bot/Dispatcher и запуск polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import ErrorEvent

from config import settings
from handlers import router
from fsm.evaluation_fsm import clear_orphaned_generation_flags, redis_client
from utils.error_notify import notify_owner

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    """Глобальный обработчик непойманных исключений в хендлерах.

    Регистрируется в Dispatcher.errors. Возвращаем True, чтобы aiogram
    считал ошибку обработанной и не пробовал другие обработчики ошибок.

    Args:
        event: Служебный апдейт с подробностями ошибки.
        bot: Экземпляр бота, через который шлём уведомление владельцу.

    Returns:
        Всегда True — ошибка считается обработанной.
    """
    await notify_owner(bot=bot, context=f"Необработанная ошибка: {event.update.update_id}", err=event.exception)
    return True


async def main() -> None:
    """Точка входа: настраивает логирование, БД, Bot и Dispatcher, запускает polling.

    Сессия бота закрывается в finally, чтобы при любой ошибке
    не оставлять открытые HTTP-соединения.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    log.info("Starting bot (mock_mode=%s)", settings.generation.MOCK_MODE)

    if not settings.bot.BOT_TOKEN:
        log.error("BOT_TOKEN is not set — startup is impossible")
        return

    # Задачи генерации рестарт не переживают, а FSM в Redis — да:
    # чистим осиротевшие флаги generating, иначе пользователь
    # останется с «Дождитесь окончания текущей генерации» навсегда
    await clear_orphaned_generation_flags()

    bot = Bot(
        token=settings.bot.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # FSM-состояния храним в Redis: данные переживают рестарт контейнера
    storage = RedisStorage.from_url(settings.redis.REDIS_URL)
    dp = Dispatcher(storage=storage)
    dp.include_router(router)  # все хендлеры собраны в один роутер пакета handlers
    dp.errors.register(on_error)

    try:
        await dp.start_polling(bot)
    finally:
        await storage.close()
        await redis_client.aclose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
