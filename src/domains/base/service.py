"""Сервисные операции домена base, не зависящие от Telegram."""

from sqlalchemy import select

from core.database import SessionLocal
from core.database.models import GenerationFeedback


async def get_last_generated_title(uid: int) -> str | None:
    """Возвращает название последней сгенерированной песни пользователя.

    Args:
        uid: Telegram user_id.

    Returns:
        Название последней генерации или None, если её нет.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            select(GenerationFeedback.title)
            .where(GenerationFeedback.user_id == uid, GenerationFeedback.title.isnot(None))
            .order_by(GenerationFeedback.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
