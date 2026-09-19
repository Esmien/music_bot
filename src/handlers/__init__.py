"""Пакет обработчиков Telegram-бота.

Собирает роутеры отдельных модулей в один общий роутер,
который подключается в bot.py: `from handlers import router`.
"""

from aiogram import Router

from handlers import auth, credits, generation

router = Router()
# Порядок важен: aiogram проверяет хендлеры по очереди,
# поэтому catch-all fallback из auth должен быть последним.
router.include_router(generation.router)
router.include_router(credits.router)
router.include_router(auth.router)
