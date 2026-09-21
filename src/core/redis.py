"""Единый клиент Redis для служебных реестров и FSM-хранилища.

Вынесен в отдельный модуль инфраструктурного ядра, чтобы любые части
проекта (fsm, handlers, bot) ссылались на один и тот же клиент,
не создавая дублирующих подключений.
"""

from redis.asyncio import Redis

from core.config import settings

# decode_responses: работаем со строками, а не с bytes
redis_client: Redis = Redis.from_url(settings.redis.REDIS_URL, decode_responses=True)
