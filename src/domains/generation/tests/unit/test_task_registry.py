"""Тесты реестра задач генерации с параллельным хранением task_id в Redis."""

import asyncio
from contextlib import suppress

from domains.generation.registries.task_registry import (
    clear_active_tasks,
    get_active_task,
    get_task_id,
    register_active_task,
    unregister_active_task,
)


async def test_register_and_get_task_id(fake_redis, monkeypatch):
    """Регистрация задачи сохраняет task_id в Redis."""
    from domains.generation.registries import task_registry

    monkeypatch.setattr(task_registry, "redis_client", fake_redis)

    user_id = 42
    task = asyncio.create_task(asyncio.sleep(0))

    task_id = await register_active_task(uid=user_id, task=task)

    # task_id должен быть uuid-строкой
    assert isinstance(task_id, str)
    assert len(task_id) == 36  # формат uuid4

    # task_id должен читаться из Redis
    stored_task_id = await get_task_id(uid=user_id)
    assert stored_task_id == task_id

    # Задача должна быть в памяти
    assert get_active_task(uid=user_id) is task

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_unregister_clears_task_id(fake_redis, monkeypatch):
    """Снятие регистрации удаляет task_id из Redis."""
    from domains.generation.registries import task_registry

    monkeypatch.setattr(task_registry, "redis_client", fake_redis)

    user_id = 99
    task = asyncio.create_task(asyncio.sleep(0))

    await register_active_task(uid=user_id, task=task)
    await unregister_active_task(uid=user_id, task=task)

    # task_id должен быть удалён
    assert await get_task_id(uid=user_id) is None
    # Задача должна быть удалена из памяти
    assert get_active_task(uid=user_id) is None

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_clear_active_tasks_removes_all_task_ids(fake_redis, monkeypatch):
    """clear_active_tasks удаляет все task_id ключи из Redis."""
    from domains.generation.registries import task_registry

    monkeypatch.setattr(task_registry, "redis_client", fake_redis)

    tasks = []
    for user_id in [10, 20, 30]:
        task = asyncio.create_task(asyncio.sleep(0))
        tasks.append(task)
        await register_active_task(uid=user_id, task=task)

    # Проверяем, что task_id записаны
    assert await get_task_id(uid=10) is not None
    assert await get_task_id(uid=20) is not None
    assert await get_task_id(uid=30) is not None

    await clear_active_tasks()

    # Все task_id должны быть удалены
    assert await get_task_id(uid=10) is None
    assert await get_task_id(uid=20) is None
    assert await get_task_id(uid=30) is None

    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
