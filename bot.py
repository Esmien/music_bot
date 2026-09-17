"""Точка входа Telegram-бота: настройка Bot/Dispatcher и запуск polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

import config
from database import init_db
from handlers import router
from handlers.utils import notify_owner

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent, bot: Bot):
    """Глобальный обработчик не пойманных исключений в хендлерах."""
    await notify_owner(bot, f"Необработанная ошибка: {event.update.update_id}", event.exception)
    return True


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    log.info("Starting bot (mock_mode=%s)", config.MOCK_MODE)

    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN не задан — запуск невозможен")
        return

    await init_db()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)  # регистрация роутера из пакета handlers
    dp.errors.register(on_error)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
