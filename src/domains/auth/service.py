"""Сервисные операции авторизации, не зависящие от Telegram."""

import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.database import User
from core.database.engine import get_session
from domains.auth.registries.auth_registry import (
    register_failed_key_attempt,
    reset_failed_key_attempts,
)

MAX_KEY_ATTEMPTS = 5


async def is_authorized(uid: int) -> bool:
    """Проверяет по БД, авторизован ли пользователь.

    Args:
        uid: Telegram user_id.

    Returns:
        True, если пользователь найден и is_authorized=True.
    """
    async with get_session() as session:
        db_user = await session.get(User, uid)
        return bool(db_user and db_user.is_authorized)


async def check_key_with_attempts(key: str, expected: str, uid: int) -> str | None:
    """Проверяет ключ доступа и учитывает лимит неудачных попыток.

    Args:
        key: Ключ, введённый пользователем.
        expected: Ожидаемый ключ доступа.
        uid: Telegram user_id.

    Returns:
        None, если ключ верный; иначе текст ошибки для пользователя.
    """

    # Позиционно: compare_digest — C-функция, именованные аргументы не принимает.
    if secrets.compare_digest(key.encode("utf-8"), expected.encode("utf-8")):
        await reset_failed_key_attempts(uid=uid)
        return None

    attempts = await register_failed_key_attempt(uid=uid)
    if attempts >= MAX_KEY_ATTEMPTS:
        await reset_failed_key_attempts(uid=uid)
        return "❌ Слишком много неверных попыток. Отправьте /start, чтобы начать заново."

    return "❌ Неверный ключ доступа."


async def mark_user_authorized(uid: int) -> None:
    """Создаёт пользователя или обновляет его статус авторизации.

    Идемпотентна: существующему пользователю проставляет флаг, нового
    создаёт. IntegrityError на flush обрабатывается на случай параллельной
    вставки того же tg_id.

    Args:
        uid: Telegram user_id.
    """
    async with get_session() as session:
        db_user = await session.get(User, uid)

        if db_user:
            db_user.is_authorized = True
        else:
            try:
                session.add(User(tg_id=uid, is_authorized=True))
                await session.flush()
            except IntegrityError:
                await session.rollback()
                result = await session.execute(select(User).where(User.tg_id == uid))
                db_user = result.scalar_one_or_none()
                if db_user:
                    db_user.is_authorized = True
        await session.commit()
