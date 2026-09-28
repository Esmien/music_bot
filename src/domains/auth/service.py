"""Сервисные операции авторизации, не зависящие от Telegram."""

import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.database import User
from core.database.engine import get_session
from core.redis import redis_client

PENDING_AUTH_KEY = "bot:pending_auth"
FAILED_KEY_ATTEMPTS_KEY = "bot:failed_key_attempts"
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


async def add_pending_auth(uid: int) -> None:
    """Отмечает пользователя как ожидающего ввод ключа доступа.

    Args:
        uid: Telegram user_id.
    """
    await redis_client.sadd(PENDING_AUTH_KEY, uid)


async def discard_pending_auth(uid: int) -> None:
    """Убирает пользователя из ожидающих ввод ключа.

    Args:
        uid: Telegram user_id.
    """
    await redis_client.srem(PENDING_AUTH_KEY, uid)


async def is_pending_auth(uid: int) -> bool:
    """Проверяет, ожидает ли пользователь ввод ключа доступа.

    Args:
        uid: Telegram user_id.

    Returns:
        True, если пользователь ожидает ввода ключа.
    """
    return bool(await redis_client.sismember(PENDING_AUTH_KEY, uid))


async def get_failed_key_attempts(uid: int) -> int:
    """Возвращает число неудачных попыток ввода ключа.

    Args:
        uid: Telegram user_id.

    Returns:
        Количество неудачных попыток (0, если записей нет).
    """
    raw = await redis_client.hget(FAILED_KEY_ATTEMPTS_KEY, uid)
    return int(raw) if raw is not None else 0


async def register_failed_key_attempt(uid: int) -> int:
    """Увеличивает счётчик неудачных попыток и возвращает новое значение.

    Args:
        uid: Telegram user_id.

    Returns:
        Количество попыток после инкремента.
    """
    return int(await redis_client.hincrby(FAILED_KEY_ATTEMPTS_KEY, uid, 1))


async def reset_failed_key_attempts(uid: int) -> None:
    """Сбрасывает счётчик неудачных попыток пользователя.

    Args:
        uid: Telegram user_id.
    """
    await redis_client.hdel(FAILED_KEY_ATTEMPTS_KEY, uid)


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
        await discard_pending_auth(uid=uid)
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
