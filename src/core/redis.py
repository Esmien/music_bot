"""Единый клиент Redis для служебных реестров и FSM-хранилища.

Вынесен в отдельный модуль инфраструктурного ядра, чтобы любые части
проекта (fsm, handlers, bot) ссылались на один и тот же клиент,
не создавая дублирующих подключений.
"""

import logging

from redis.asyncio import Redis

from core.config import settings

log = logging.getLogger(__name__)

# decode_responses: работаем со строками, а не с bytes
redis_client: Redis = Redis.from_url(settings.redis.redis_url, decode_responses=True)

GENERATION_CANCEL_KEY_PREFIX = "bot:cancel:gen"


def generation_cancel_key(gen_id: int) -> str:
    """Возвращает Redis-ключ отмены генерации.

    Args:
        gen_id: ID генерации в базе данных.

    Returns:
        Ключ Redis для cancel-token.
    """
    return f"{GENERATION_CANCEL_KEY_PREFIX}:{gen_id}"


async def request_generation_cancel(gen_id: int, *, ttl_seconds: int = 600) -> None:
    """Устанавливает cancel-token для генерации.

    Args:
        gen_id: ID генерации в базе данных.
        ttl_seconds: Время жизни токена в Redis.
    """
    await redis_client.set(generation_cancel_key(gen_id), "1", ex=ttl_seconds)


async def is_generation_cancelled(gen_id: int) -> bool:
    """Проверяет cancel-token генерации.

    Args:
        gen_id: ID генерации в базе данных.

    Returns:
        True, если генерация отменена.
    """
    return bool(await redis_client.exists(generation_cancel_key(gen_id)))


async def clear_generation_cancel(gen_id: int) -> None:
    """Удаляет cancel-token после завершения генерации.

    Args:
        gen_id: ID генерации в базе данных.
    """
    await redis_client.delete(generation_cancel_key(gen_id))


async def shutdown_redis() -> None:
    """Закрывает соединение с Redis.

    Идемпотентна: повторный вызов безопасен.
    """
    try:
        await redis_client.aclose()
    except Exception as e:
        log.error("Failed to close Redis connection: %s", e)
        raise
