"""Сохранение пользовательских оценок и отзывов о генерациях."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core.database import SessionLocal
from core.database.models import GenerationFeedback

log = logging.getLogger(__name__)


async def save_feedback(user_id: int, feedback: str | None, evalue: bool) -> None:
    """Сохраняет оценку и/или текстовый отзыв о последней генерации.

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
        async with SessionLocal() as session:
            result = await session.execute(
                select(GenerationFeedback)
                .where(GenerationFeedback.user_id == user_id)
                .order_by(GenerationFeedback.id.desc())
                .limit(1)
            )
            record = result.scalar_one_or_none()
            if record is None:
                # DEVIATION: запись генерации может отсутствовать, если сохранение
                # названия упало; создаём минимальную запись ради сохранения оценки.
                record = GenerationFeedback(
                    user_id=user_id,
                    initial_prompt="",
                    enriched_prompt="",
                    title=None,
                )
                session.add(record)

            record.is_liked = evalue
            if feedback:
                record.feedback = feedback
            await session.commit()
    except SQLAlchemyError:
        log.error("Failed to save feedback (user=%s)", user_id, exc_info=True)
