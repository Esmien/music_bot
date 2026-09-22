"""Пакет обработчиков Telegram-бота.

Собирает роутеры отдельных модулей в один общий роутер,
который подключается в bot.py: `from handlers import router`.
"""

from aiogram import Router

from handlers import auth, base_handlers, credits_handlers, generation_handlers

router = Router()
# Порядок важен: aiogram проверяет хендлеры по очереди.
# base_handlers первым — универсальная отмена не должна перехватываться
# сценарными хендлерами, а catch-all fallback из auth — последним.
router.include_router(base_handlers.router)
router.include_router(generation_handlers.router)
router.include_router(credits_handlers.router)
router.include_router(auth.router)
