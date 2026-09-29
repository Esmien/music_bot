"""Сохранение пользовательских оценок и отзывов о генерациях."""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from core.database.engine import get_session
from core.utils.exceptions import FeedbackSaveError
from domains.feedback.models import GenerationFeedback
from domains.generation.models import Generation, GenerationStatus

log = logging.getLogger(__name__)


async def save_feedback(
    *,
    gen_id: int,
    user_id: int,
    feedback: str | None = None,
    evalue: bool | None = None,
) -> None:
    """Сохраняет оценку и/или текстовый отзыв для конкретной генерации пользователя.

    Проверяет, что генерация с gen_id принадлежит указанному user_id и завершена успешно.
    Использует PostgreSQL upsert (INSERT ... ON CONFLICT DO UPDATE) для безопасного
    обновления записи при конкурентных вызовах. Если запись существует, обновляет
    только переданные поля (is_liked и/или feedback).

    Args:
        gen_id: ID генерации в БД.
        user_id: Telegram user_id пользователя.
        feedback: Текст отзыва или None (не обновляется, если None).
        evalue: True — лайк, False — дизлайк, None — не обновляется.

    Returns:
        None.

    Raises:
        FeedbackSaveError: При ошибке записи в БД.
    """
    if feedback is None and evalue is None:
        return

    try:
        async with get_session() as session:
            generation_result = await session.execute(
                select(Generation).where(
                    Generation.id == gen_id,
                    Generation.user_id == user_id,
                    Generation.status == GenerationStatus.SUCCESS,
                )
            )
            generation = generation_result.scalar_one_or_none()
            if generation is None:
                log.info(
                    "Feedback was not saved because generation does not exist, "
                    "is not successful or does not belong to user (gen_id=%s, user=%s)",
                    gen_id,
                    user_id,
                )
                return

            values_to_insert: dict[str, Any] = {"generation_id": generation.id}
            values_to_update: dict[str, Any] = {}

            if evalue is not None:
                values_to_insert["is_liked"] = evalue
                values_to_update["is_liked"] = evalue
            if feedback is not None:
                values_to_insert["feedback"] = feedback
                values_to_update["feedback"] = feedback

            stmt = insert(GenerationFeedback).values(**values_to_insert)
            stmt = stmt.on_conflict_do_update(
                index_elements=["generation_id"],
                set_=values_to_update,
            )

            await session.execute(stmt)
            await session.commit()
    except SQLAlchemyError as exc:
        log.exception("Failed to save feedback (gen_id=%s, user=%s)", gen_id, user_id)
        raise FeedbackSaveError(f"Failed to save feedback for gen_id={gen_id}") from exc
