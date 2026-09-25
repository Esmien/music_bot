"""Пакет обработчиков Telegram-бота."""

from aiogram import Router

from domains.auth.handlers import router as auth_router
from domains.base.handlers import router as base_router
from domains.credits.handlers import router as credits_router
from domains.enricher.handlers import router as enricher_router
from domains.evaluation.handlers import router as evaluation_router
from domains.generation.handlers import router as generation_router
from handlers.feedback_handlers import router as feedback_router

router = Router()
router.include_router(base_router)
router.include_router(generation_router)
router.include_router(enricher_router)
router.include_router(evaluation_router)
router.include_router(feedback_router)
router.include_router(credits_router)
router.include_router(auth_router)
