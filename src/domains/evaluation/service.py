"""Сохранение пользовательских оценок и отзывов о генерациях."""

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from core.database.engine import get_session
from domains.feedback.models import GenerationFeedback
from domains.generation.models import Generation, GenerationStatus

log = logging.getLogger(__name__)


async def save_evaluation(user_id: int, is_liked: bool) -> None:
    """Сохраняет оценку (лайк/дизлайк) для последней успешной генерации пользователя.

    Использует PostgreSQL upsert для безопасной записи при конкурентных вызовах.

    Args:
        user_id: Telegram user_id пользователя.
        is_liked: True — лайк, False — дизлайк.

    Returns:
        None.

    Raises:
        SQLAlchemyError: При ошибке записи в БД (логируется, но не пробрасывается).
    """
    try:
        async with get_session() as session:
            generation_result = await session.execute(
                select(Generation)
                .where(
                    Generation.user_id == user_id,
                    Generation.status == GenerationStatus.SUCCESS,
                )
                .order_by(Generation.created_at.desc(), Generation.id.desc())
                .limit(1)
            )
            generation = generation_result.scalar_one_or_none()
            if generation is None:
                log.info("Evaluation was not saved because no successful generation exists (user=%s)", user_id)
                return

            stmt = insert(GenerationFeedback).values(generation_id=generation.id, is_liked=is_liked)
            stmt = stmt.on_conflict_do_update(
                index_elements=["generation_id"],
                set_={"is_liked": is_liked},
            )

            await session.execute(stmt)
            await session.commit()
    except SQLAlchemyError:
        log.exception("Failed to save evaluation (user=%s)", user_id, exc_info=True)
