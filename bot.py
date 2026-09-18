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
    dp.include_router(router)  # все хендлеры собраны в один роутер пакета handlers
    dp.errors.register(on_error)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
