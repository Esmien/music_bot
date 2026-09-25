"""Сохранение пользовательских оценок и отзывов о генерациях."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core.database.engine import get_session
from domains.feedback.models import GenerationFeedback
from domains.generation.models import Generation, GenerationStatus

log = logging.getLogger(__name__)


async def save_feedback(user_id: int, feedback: str | None, evalue: bool) -> None:
    """Сохраняет оценку и/или текстовый отзыв о последней успешной генерации.

    Args:
        user_id: Telegram user_id пользователя.
        feedback: Текст отзыва или None.
        evalue: True — лайк, False — дизлайк.

    Returns:
        None.
    """
    if not feedback and not evalue:
        return

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
                log.info("Feedback was not saved because no successful generation exists (user=%s)", user_id)
                return

            feedback_result = await session.execute(
                select(GenerationFeedback).where(GenerationFeedback.generation_id == generation.id).limit(1)
            )
            record = feedback_result.scalar_one_or_none()
            if record is None:
                record = GenerationFeedback(generation_id=generation.id)
                session.add(record)

            record.is_liked = evalue
            if feedback:
                record.feedback = feedback
            await session.commit()
    except SQLAlchemyError:
        log.error("Failed to save feedback (user=%s)", user_id, exc_info=True)
