"""Реестр живых задач генерации по user_id.

Единая точка реестров — Redis: набор активных user_id переживает
перезапуск процесса. Сам asyncio.Task в Redis не сериализуется, поэтому
он хранится в памяти процесса, а Redis-набор хранит только uid тех, кто
сейчас генерирует. На старте бота набор чистится clear_active_tasks,
а след в FSM — clear_orphaned_generation_flags.
"""

import asyncio

from core.redis import redis_client

# Ключ множества uid с живой задачей генерации
ACTIVE_TASKS_KEY = "bot:active_tasks"

# Живые задачи в памяти процесса: asyncio.Task не сериализуется в Redis
_active_tasks: dict[int, asyncio.Task] = {}


async def register_active_task(uid: int, task: asyncio.Task) -> None:
    """Регистрирует живую задачу генерации пользователя.

    Args:
        uid: Telegram user_id.
        task: Фоновая задача генерации.
    """
    _active_tasks[uid] = task
    await redis_client.sadd(ACTIVE_TASKS_KEY, uid)


async def unregister_active_task(uid: int, task: asyncio.Task) -> None:
    """Снимает регистрацию задачи, если она всё ещё актуальна.

    Запись задачи, стартовавшей позже, не трогаем.

    Args:
        uid: Telegram user_id.
        task: Задача, которую снимаем с учёта.
    """
    if _active_tasks.get(uid) is task:
        _active_tasks.pop(uid, None)
        await redis_client.srem(ACTIVE_TASKS_KEY, uid)


def get_active_task(uid: int) -> asyncio.Task | None:
    """Возвращает живую задачу генерации пользователя, если она есть.

    Args:
        uid: Telegram user_id.

    Returns:
        Задача генерации или None.
    """
    return _active_tasks.get(uid)


async def clear_active_tasks() -> int:
    """Чистит реестр активных задач в памяти и в Redis на старте бота.

    Задачи генерации рестарт не переживают: без чистки Redis-набор
    остался бы с uid, которых в памяти процесса уже нет.

    Returns:
        Количество удалённых ключей Redis (0 или 1).
    """
    _active_tasks.clear()
    return await redis_client.delete(ACTIVE_TASKS_KEY)
