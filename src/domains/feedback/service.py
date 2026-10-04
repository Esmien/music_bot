"""Сохранение пользовательских оценок и отзывов о генерациях."""

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.engine import get_session
from core.utils.exceptions import FeedbackSaveError
from domains.feedback.models import GenerationFeedback
from domains.generation.models import Generation, GenerationStatus

log = logging.getLogger(__name__)


def _build_upsert_stmt(
    *,
    session: AsyncSession,
    generation_id: int,
    evalue: bool | None,
    feedback: str | None,
) -> Any:
    """Формирует upsert-выражение для сохранения оценки и отзыва.

    Args:
        session: Текущая сессия SQLAlchemy.
        generation_id: ID генерации.
        evalue: Оценка (True — лайк, False — дизлайк, None — без изменений).
        feedback: Текст отзыва или None.

    Returns:
        Сконструированное SQL-выражение с обработкой конфликта.
    """
    bind = getattr(session, "bind", None)
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    insert_fn = pg_insert if "postgresql" in dialect_name else sqlite_insert

    stmt = insert_fn(GenerationFeedback).values(
        generation_id=generation_id,
        is_liked=evalue,
        feedback=feedback,
    )
    update_dict: dict[str, Any] = {}
    if evalue is not None:
        update_dict["is_liked"] = evalue
    if feedback is not None:
        update_dict["feedback"] = feedback

    if update_dict:
        return stmt.on_conflict_do_update(
            index_elements=["generation_id"],
            set_=update_dict,
        )
    return stmt.on_conflict_do_nothing(index_elements=["generation_id"])


async def _save_feedback_in_session(
    *,
    gen_id: int,
    user_id: int,
    feedback: str | None,
    evalue: bool | None,
) -> None:
    """Выполняет проверку генерации и upsert отзыва в рамках сессии.

    Args:
        gen_id: ID генерации в БД.
        user_id: Telegram user_id пользователя.
        feedback: Текст отзыва или None.
        evalue: Оценка или None.

    Returns:
        None.
    """
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

        stmt = _build_upsert_stmt(
            session=session,
            generation_id=generation.id,
            evalue=evalue,
            feedback=feedback,
        )
        await session.execute(stmt)
        await session.commit()


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
            await _save_feedback_in_session(
                gen_id=gen_id,
                user_id=user_id,
                feedback=feedback,
                evalue=evalue,
            )
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
