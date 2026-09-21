"""Реестр пользователей, ожидающих ввода ключа доступа.

Вынесено из evaluation_fsm.py: реестр не относится к FSM-состояниям
обогащения промпта. Как и FSM-состояния, хранится в Redis и переживает
рестарт контейнера.
"""

from core.redis import redis_client

# Ключ множества пользователей, ожидающих ввода ключа доступа
PENDING_AUTH_KEY = "bot:pending_auth"


async def add_pending_auth(uid: int) -> None:
    """Отмечает пользователя как ожидающего ввод ключа доступа.

    Args:
        uid: Telegram user_id.
    """
    await redis_client.sadd(PENDING_AUTH_KEY, uid)


async def discard_pending_auth(uid: int) -> None:
    """Убирает пользователя из ожидающих ввод ключа (идемпотентно).

    Args:
        uid: Telegram user_id.
    """
    await redis_client.srem(PENDING_AUTH_KEY, uid)


async def is_pending_auth(uid: int) -> bool:
    """Проверяет, ожидает ли пользователь ввод ключа доступа.

    Args:
        uid: Telegram user_id.

    Returns:
        True, если пользователь в реестре ожидающих.
    """
    return bool(await redis_client.sismember(PENDING_AUTH_KEY, uid))
