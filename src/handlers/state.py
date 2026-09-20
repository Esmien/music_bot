"""Общее состояние между обработчиками, без циклических импортов.

Вынесено в отдельный модуль, чтобы auth и filters могли ссылаться
на одни и те же данные, не импортируя друг друга.

pending_auth хранится в Redis: как и FSM-состояния, реестр ожидающих
ключ переживает рестарт контейнера. active_tasks остаётся в памяти —
asyncio.Task не сериализуется, а задачи при рестарте теряются в любом
случае; осиротевшие флаги generating вычищаются на старте бота.
"""

import asyncio
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from config import settings

log = logging.getLogger(__name__)

# Единый клиент Redis для служебных реестров и чистки FSM.
# decode_responses: работаем со строками, а не с bytes
redis_client: Redis = Redis.from_url(settings.redis.REDIS_URL, decode_responses=True)

# Ключ множества пользователей, ожидающих ввода ключа доступа
PENDING_AUTH_KEY = "bot:pending_auth"

# Префикс ключей FSM-хранилища aiogram (RedisStorage по умолчанию)
_FSM_KEY_MATCH = "fsm:*"


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


async def clear_orphaned_generation_flags() -> int:
    """Чистит осиротевшие флаги generating в FSM после перезапуска бота.

    FSM-состояния живут в Redis и переживают рестарт, а задачи генерации —
    нет: без чистки пользователь навсегда оставался бы с «Дождитесь
    окончания текущей генерации». Вызывается на старте, когда active_tasks
    ещё пуст, поэтому любой выставленный флаг считается осиротевшим.
    Данные FSM RedisStorage хранит как JSON в hash-поле data.

    Returns:
        Количество очищенных FSM-записей.
    """
    cleaned = 0
    try:
        async for key in redis_client.scan_iter(match=_FSM_KEY_MATCH):
            raw = await redis_client.hget(key, "data")
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                log.warning("Skipping non-JSON FSM data under key %s", key)
                continue
            if not isinstance(data, dict) or not data.get("generating"):
                continue
            data.pop("generating", None)
            data.pop("gen_id", None)
            await redis_client.hset(key, "data", json.dumps(data))
            cleaned += 1
            log.info("Cleared orphaned generation flag (key=%s)", key)
    except RedisError:
        log.exception("Failed to clean orphaned generation flags in Redis")
    return cleaned


# Живые задачи генерации по user_id: позволяют честно погасить генерацию
# из cmd_cancel_generation и cmd_logout. Сам asyncio.Task в FSM-данные не
# положишь (не сериализуется), поэтому реестр живёт здесь; при перезапуске
# процесса задачи теряются, а их след в FSM чистит clear_orphaned_generation_flags.
active_tasks: dict[int, asyncio.Task] = {}
