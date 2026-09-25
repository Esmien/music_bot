"""Реестр пользователей, ожидающих ввода ключа доступа.

Вынесено из evaluation_fsm.py: реестр не относится к FSM-состояниям
обогащения промпта. Как и FSM-состояния, хранится в Redis и переживает
рестарт контейнера.
"""

from core.redis import redis_client

# Ключ множества пользователей, ожидающих ввода ключа доступа
PENDING_AUTH_KEY = "bot:pending_auth"

# Ключ хэша со счётчиками неудачных попыток ввода ключа доступа
FAILED_KEY_ATTEMPTS_KEY = "bot:failed_key_attempts"


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


async def get_failed_key_attempts(uid: int) -> int:
    """Возвращает число неудачных попыток ввода ключа для пользователя.

    Args:
        uid: Telegram user_id.

    Returns:
        Количество неудачных попыток (0, если записи нет).
    """
    raw = await redis_client.hget(FAILED_KEY_ATTEMPTS_KEY, uid)
    return int(raw) if raw is not None else 0


async def register_failed_key_attempt(uid: int) -> int:
    """Увеличивает счётчик неудачных попыток и возвращает новое значение.

    Args:
        uid: Telegram user_id.

    Returns:
        Количество неудачных попыток после инкремента.
    """
    return int(await redis_client.hincrby(FAILED_KEY_ATTEMPTS_KEY, uid, 1))


async def reset_failed_key_attempts(uid: int) -> None:
    """Сбрасывает счётчик неудачных попыток пользователя (идемпотентно).

    Args:
        uid: Telegram user_id.
    """
    await redis_client.hdel(FAILED_KEY_ATTEMPTS_KEY, uid)
