"""Пакет обработчиков Telegram-бота."""

from aiogram import Router

from domains.auth.handlers import router as auth_router
from domains.base.handlers import router as base_router
from handlers import (
    credits_handlers,
    enricher_handlers,
    evaluation_handlers,
    feedback_handlers,
    generation_handlers,
)

router = Router()
router.include_router(base_router)
router.include_router(generation_handlers.router)
router.include_router(enricher_handlers.router)
router.include_router(evaluation_handlers.router)
router.include_router(feedback_handlers.router)
router.include_router(credits_handlers.router)
router.include_router(auth_router)
