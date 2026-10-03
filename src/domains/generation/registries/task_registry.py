"""Реестр живых задач генерации по user_id.

Каждый экземпляр бота хранит свой набор активных user_id в Redis
под ключом bot:active_tasks:{instance_id}. Это защищает от race condition
при rolling restart: один экземпляр не удаляет задачи другого.

asyncio.Task не сериализуется в Redis, поэтому хранится в памяти процесса.
Redis-набор хранит только uid тех, кто сейчас генерирует.

При старте cleanup чистит только собственный набор. При shutdown также
чистим только свои задачи. FSM-флаги generating чистятся отдельно через
clear_orphaned_generation_flags.
"""

import asyncio
import logging

from core.instance import current_instance
from core.redis import redis_client  # type: ignore[attr-defined]

log = logging.getLogger(__name__)


def _active_tasks_key(instance_id: str) -> str:
    """Формирует Redis-ключ набора активных задач для конкретного экземпляра.

    Args:
        instance_id: Уникальный идентификатор экземпляра бота.

    Returns:
        Redis-ключ формата bot:active_tasks:{instance_id}.
    """
    return f"bot:active_tasks:{instance_id}"


# Живые задачи в памяти процесса: asyncio.Task не сериализуется в Redis
_active_tasks: dict[int, asyncio.Task] = {}


async def register_active_task(uid: int, task: "asyncio.Task[None]") -> None:
    """Регистрирует живую задачу генерации пользователя.

    Записывает задачу в память процесса и добавляет user_id в Redis-набор
    текущего экземпляра.

    Args:
        uid: Telegram user_id.
        task: Фоновая задача генерации.
    """
    _active_tasks[uid] = task
    key = _active_tasks_key(current_instance.instance_id)
    await redis_client.sadd(key, uid)
    log.debug("Registered active task for user_id=%d, instance_id=%s", uid, current_instance.instance_id)


async def unregister_active_task(uid: int, task: "asyncio.Task[None]") -> None:
    """Снимает регистрацию задачи, если она всё ещё актуальна.

    Запись задачи, стартовавшей позже, не трогаем.

    Args:
        uid: Telegram user_id.
        task: Задача, которую снимаем с учёта.
    """
    if _active_tasks.get(uid) is task:
        _active_tasks.pop(uid, None)
        key = _active_tasks_key(current_instance.instance_id)
        await redis_client.srem(key, uid)
        log.debug("Unregistered active task for user_id=%d, instance_id=%s", uid, current_instance.instance_id)


def get_active_task(uid: int) -> "asyncio.Task[None] | None":
    """Возвращает живую задачу генерации пользователя, если она есть.

    Args:
        uid: Telegram user_id.

    Returns:
        Задача генерации или None.
    """
    return _active_tasks.get(uid)


async def clear_active_tasks() -> dict[str, int]:
    """Чистит реестр активных задач текущего экземпляра в памяти и в Redis.

    Вызывается при старте и shutdown. Задачи генерации рестарт не переживают:
    без чистки Redis-набор остался бы с uid, которых в памяти процесса уже нет.

    Cleanup всегда ownership-safe: каждый экземпляр чистит только свой набор
    bot:active_tasks:{instance_id}, не трогая задачи других экземпляров.

    Returns:
        Словарь с результатами: {"deleted_count": int, "memory_cleared": int}.

    Raises:
        RedisError: При ошибке работы с Redis.
    """
    memory_count = len(_active_tasks)
    _active_tasks.clear()

    key = _active_tasks_key(current_instance.instance_id)
    deleted_count = await redis_client.delete(key)

    log.info(
        "Active tasks registry cleaned: instance_id=%s, memory_cleared=%d, redis_deleted=%d",
        current_instance.instance_id,
        memory_count,
        deleted_count,
    )
    return {"deleted_count": deleted_count, "memory_cleared": memory_count}
