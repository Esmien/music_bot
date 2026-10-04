"""Единый клиент Redis для служебных реестров и FSM-хранилища.

Модуль предоставляет единую точку подключения к Redis для FSM-хранилища
и служебных реестров (cancel-токены генераций).
Все компоненты проекта используют общий redis_client.
"""

import logging
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from core.config import settings

log = logging.getLogger(__name__)

# decode_responses: работаем со строками вместо bytes
if TYPE_CHECKING:
    redis_client: Redis[str]
else:
    redis_client = Redis.from_url(
        url=settings.redis.redis_url,
        decode_responses=True,
    )

# Source of truth для запроса отмены генерации пользователем.
# Этот токен проверяется воркером на этапах PENDING → PROCESSING.
GENERATION_CANCEL_KEY_PREFIX = "bot:cancel:gen"

# Source of truth для запроса пропуска доставки после SUCCESS.
# Этот токен проверяется воркером только перед delivery.
DELIVERY_CANCEL_KEY_PREFIX = "bot:cancel:delivery"

# Lua-скрипт для атомарного освобождения лока по токену владельца
RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def generation_cancel_key(gen_id: int) -> str:
    """Возвращает Redis-ключ отмены генерации (PENDING/PROCESSING → CANCELLED).

    Args:
        gen_id: ID генерации в базе данных.

    Returns:
        Ключ Redis для generation cancel-token.
    """
    return f"{GENERATION_CANCEL_KEY_PREFIX}:{gen_id}"


def delivery_cancel_key(gen_id: int) -> str:
    """Возвращает Redis-ключ отмены доставки (SUCCESS остаётся SUCCESS, delivery пропускается).

    Args:
        gen_id: ID генерации в базе данных.

    Returns:
        Ключ Redis для delivery cancel-token.
    """
    return f"{DELIVERY_CANCEL_KEY_PREFIX}:{gen_id}"


async def request_generation_cancel(*, gen_id: int, ttl_seconds: int = 600) -> None:
    """Устанавливает cancel-token для генерации (source of truth для отмены).

    TTL 600 секунд покрывает максимальное время генерации (~3 мин) + запас на retry.
    Токен автоматически удаляется Redis через TTL либо явно воркером после обработки отмены.

    Args:
        gen_id: ID генерации в базе данных.
        ttl_seconds: Время жизни токена в Redis (по умолчанию 10 минут).

    Returns:
        None.
    """
    await redis_client.set(name=generation_cancel_key(gen_id), value="1", ex=ttl_seconds)


async def is_generation_cancelled(gen_id: int) -> bool:
    """Проверяет cancel-token генерации.

    Args:
        gen_id: ID генерации в базе данных.

    Returns:
        True если генерация отменена, иначе False.
    """
    result: int = await redis_client.exists(generation_cancel_key(gen_id=gen_id))
    return bool(result)


async def clear_generation_cancel(gen_id: int) -> None:
    """Удаляет generation cancel-token после завершения обработки отмены.

    Args:
        gen_id: ID генерации в базе данных.

    Returns:
        None.
    """
    await redis_client.delete(generation_cancel_key(gen_id))


async def request_delivery_cancel(*, gen_id: int, ttl_seconds: int = 600) -> None:
    """Устанавливает delivery cancel-token для пропуска доставки после SUCCESS.

    Используется когда пользователь отменяет генерацию после сохранения MP3.
    Статус генерации остаётся SUCCESS, но доставка в Telegram пропускается.

    Args:
        gen_id: ID генерации в базе данных.
        ttl_seconds: Время жизни токена в Redis (по умолчанию 10 минут).

    Returns:
        None.
    """
    await redis_client.set(name=delivery_cancel_key(gen_id), value="1", ex=ttl_seconds)


async def is_delivery_cancelled(gen_id: int) -> bool:
    """Проверяет delivery cancel-token.

    Args:
        gen_id: ID генерации в базе данных.

    Returns:
        True если доставка отменена, иначе False.
    """
    result: int = await redis_client.exists(delivery_cancel_key(gen_id=gen_id))
    return bool(result)


async def clear_delivery_cancel(gen_id: int) -> None:
    """Удаляет delivery cancel-token после завершения обработки.

    Args:
        gen_id: ID генерации в базе данных.

    Returns:
        None.
    """
    await redis_client.delete(delivery_cancel_key(gen_id))


async def shutdown_redis() -> None:
    """Закрывает соединение с Redis.

    Идемпотентна: повторный вызов безопасен.

    Raises:
        Exception: При ошибке закрытия соединения (логируется и пробрасывается).
    """
    try:
        await redis_client.aclose()
    except Exception as e:
        log.exception("Failed to close Redis connection: %s", e)
        raise
