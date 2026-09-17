"""Пакет обработчиков Telegram-бота.

Собирает роутеры отдельных модулей в один общий роутер,
который подключается в bot.py: `from handlers import router`.
"""

from aiogram import Router

from . import auth, credits, generation

router = Router()
# Порядок важен: обработчики с состояниями и команды должны быть
# зарегистрированы раньше catch-all fallback из auth.
router.include_router(generation.router)
router.include_router(credits.router)
router.include_router(auth.router)
