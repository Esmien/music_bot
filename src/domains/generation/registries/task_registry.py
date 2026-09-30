"""Реестр живых задач генерации по user_id.

Единая точка реестров — Redis: набор активных user_id переживает
перезапуск процесса. Сам asyncio.Task в Redis не сериализуется, поэтому
он хранится в памяти процесса, а Redis-набор хранит только uid тех, кто
сейчас генерирует. На старте бота набор чистится clear_active_tasks,
а след в FSM — clear_orphaned_generation_flags.

Параллельно в Redis хранится task_id каждой генерации (строковый uid задачи) —
задел под отмену воркеров в будущем: воркер сможет проверить актуальность
task_id перед отправкой результата или отменить задачу по внешнему сигналу.
"""

import asyncio
import uuid

from core.redis import redis_client  # type: ignore[attr-defined]

# Ключ множества uid с живой задачей генерации
ACTIVE_TASKS_KEY = "bot:active_tasks"

# Префикс ключей task_id: bot:task_id:{user_id} -> строковый uid задачи
TASK_ID_KEY_PREFIX = "bot:task_id"

# Живые задачи в памяти процесса: asyncio.Task не сериализуется в Redis
_active_tasks: dict[int, asyncio.Task] = {}


async def register_active_task(uid: int, task: "asyncio.Task[None]") -> str:
    """Регистрирует живую задачу генерации пользователя.

    Args:
        uid: Telegram user_id.
        task: Фоновая задача генерации.

    Returns:
        Строковый uid задачи (task_id), сохранённый в Redis.
    """
    task_id = str(uuid.uuid4())
    _active_tasks[uid] = task
    await redis_client.sadd(ACTIVE_TASKS_KEY, uid)
    await redis_client.set(f"{TASK_ID_KEY_PREFIX}:{uid}", task_id)
    return task_id


async def unregister_active_task(uid: int, task: "asyncio.Task[None]") -> None:
    """Снимает регистрацию задачи, если она всё ещё актуальна.

    Запись задачи, стартовавшей позже, не трогаем.

    Args:
        uid: Telegram user_id.
        task: Задача, которую снимаем с учёта.
    """
    if _active_tasks.get(uid) is task:
        _active_tasks.pop(uid, None)
        await redis_client.srem(ACTIVE_TASKS_KEY, uid)
        await redis_client.delete(f"{TASK_ID_KEY_PREFIX}:{uid}")


def get_active_task(uid: int) -> "asyncio.Task[None] | None":
    """Возвращает живую задачу генерации пользователя, если она есть.

    Args:
        uid: Telegram user_id.

    Returns:
        Задача генерации или None.
    """
    return _active_tasks.get(uid)


async def get_task_id(uid: int) -> str | None:
    """Возвращает строковый uid задачи генерации пользователя из Redis.

    Args:
        uid: Telegram user_id.

    Returns:
        Строковый task_id или None, если задача не зарегистрирована.
    """
    task_id = await redis_client.get(f"{TASK_ID_KEY_PREFIX}:{uid}")
    if not task_id:
        return None
    # fakeredis возвращает строки, реальный Redis — байты
    return task_id.decode("utf-8") if isinstance(task_id, bytes) else task_id


async def clear_active_tasks() -> int:
    """Чистит реестр активных задач в памяти и в Redis на старте бота.

    Задачи генерации рестарт не переживают: без чистки Redis-набор
    остался бы с uid, которых в памяти процесса уже нет.

    Returns:
        Количество удалённых ключей Redis (0 или 1).
    """
    _active_tasks.clear()
    # Чистим набор активных uid
    deleted_set = await redis_client.delete(ACTIVE_TASKS_KEY)
    # Чистим все task_id ключи через паттерн
    cursor = 0
    while True:  # type: ignore[unreachable]
        cursor, keys = await redis_client.scan(cursor, match=f"{TASK_ID_KEY_PREFIX}:*", count=100)
        if keys:
            await redis_client.delete(*keys)
        if cursor == 0:
            break
    return deleted_set
