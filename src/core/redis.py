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


async def shutdown_redis() -> None:
    """Закрывает соединение с Redis.

    Идемпотентна: повторный вызов безопасен.
    """
    try:
        await redis_client.aclose()
    except Exception as e:
        log.error("Failed to close Redis connection: %s", e)
        raise
