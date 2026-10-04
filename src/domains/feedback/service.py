"""Сохранение пользовательских оценок и отзывов о генерациях."""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

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
    Использует атомарный upsert через INSERT ON CONFLICT (PostgreSQL или SQLite)
    для защиты от race condition при параллельных callback.
    Если запись существует, обновляет только переданные поля (is_liked и/или feedback),
    не затирая существующие значения.

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

    max_attempts = 5
    for attempt in range(max_attempts):
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

                bind = getattr(session, "bind", None)
                dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
                insert_fn = pg_insert if "postgresql" in dialect_name else sqlite_insert

                stmt = insert_fn(GenerationFeedback).values(
                    generation_id=generation.id,
                    is_liked=evalue,
                    feedback=feedback,
                )
                update_dict = {}
                if evalue is not None:
                    update_dict["is_liked"] = evalue
                if feedback is not None:
                    update_dict["feedback"] = feedback

                if update_dict:
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["generation_id"],
                        set_=update_dict,
                    )
                else:
                    stmt = stmt.on_conflict_do_nothing(index_elements=["generation_id"])

                await session.execute(stmt)
                await session.commit()
                return
        except (OperationalError, IntegrityError) as exc:
            if attempt < max_attempts - 1:
                await asyncio.sleep(0.02 * (attempt + 1))
                continue
            log.exception("Concurrency conflict while saving feedback (gen_id=%s, user=%s)", gen_id, user_id)
            raise FeedbackSaveError(f"Failed to save feedback for gen_id={gen_id}") from exc
        except SQLAlchemyError as exc:
            log.exception("Failed to save feedback (gen_id=%s, user=%s)", gen_id, user_id)
            raise FeedbackSaveError(f"Failed to save feedback for gen_id={gen_id}") from exc
