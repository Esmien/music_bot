"""Ядро приложения: сборка доменных роутеров и регистрация воркеров.

Модуль объединяет все доменные роутеры в единый Router для Dispatcher
и предоставляет точку входа для регистрации воркеров TaskIQ.

Роутеры подключаются в порядке приоритета: сначала base (высший приоритет),
затем остальные домены, в конце auth (ловит необработанные сообщения).
"""

from aiogram import Router

from domains.auth.handlers import router as auth_router
from domains.base.handlers import router as base_router
from domains.credits.handlers import router as credits_router
from domains.enricher.handlers import router as enricher_router
from domains.evaluation.handlers import router as evaluation_router
from domains.feedback.handlers import router as feedback_router
from domains.generation.handlers import router as generation_router

router = Router()
router.include_router(base_router)
router.include_router(generation_router)
router.include_router(enricher_router)
router.include_router(evaluation_router)
router.include_router(feedback_router)
router.include_router(credits_router)
router.include_router(auth_router)
