"""Сервисные операции домена base, не зависящие от Telegram."""

from sqlalchemy import select

from core.database import SessionLocal
from domains.generation.models import Generation, GenerationStatus


async def get_last_generated_title(uid: int) -> str | None:
    """Возвращает название последней успешно сгенерированной песни пользователя.

    Args:
        uid: Telegram user_id.

    Returns:
        Название последней успешной генерации или None, если её нет.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            select(Generation.title)
            .where(
                Generation.user_id == uid,
                Generation.status == GenerationStatus.SUCCESS,
                Generation.title.isnot(None),
            )
            .order_by(Generation.created_at.desc(), Generation.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
