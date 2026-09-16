import asyncio
import logging

from aiogram import Bot, Dispatcher

import config
from database import init_db
from middlewares.auth import AuthMiddleware
from handlers import router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
dp.message.middleware(AuthMiddleware())
dp.include_router(router)


async def main():
    await init_db()
    log.info("Bot starting. MOCK_MODE=%s", config.MOCK_MODE)
    if not config.BOT_ACCESS_KEY:
        log.warning("BOT_ACCESS_KEY is empty — авторизация не сработает корректно!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
