"""Тесты реестра задач генерации."""

import asyncio
from contextlib import suppress

from domains.generation.registries.task_registry import (
    clear_active_tasks,
    get_active_task,
    register_active_task,
    unregister_active_task,
)


async def test_register_and_get_active_task(fake_redis, monkeypatch):
    """Регистрация задачи добавляет её в реестр и Redis."""
    from domains.generation.registries import task_registry

    monkeypatch.setattr(task_registry, "redis_client", fake_redis)

    user_id = 42
    task = asyncio.create_task(asyncio.sleep(0))

    await register_active_task(uid=user_id, task=task)

    # Задача должна быть в памяти
    assert get_active_task(uid=user_id) is task

    # uid должен быть в Redis-множестве
    is_member = await fake_redis.sismember("bot:active_tasks", user_id)
    assert is_member

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_unregister_removes_task(fake_redis, monkeypatch):
    """Снятие регистрации удаляет задачу из реестра и Redis."""
    from domains.generation.registries import task_registry

    monkeypatch.setattr(task_registry, "redis_client", fake_redis)

    user_id = 99
    task = asyncio.create_task(asyncio.sleep(0))

    await register_active_task(uid=user_id, task=task)
    await unregister_active_task(uid=user_id, task=task)

    # Задача должна быть удалена из памяти
    assert get_active_task(uid=user_id) is None

    # uid должен быть удалён из Redis-множества
    is_member = await fake_redis.sismember("bot:active_tasks", user_id)
    assert not is_member

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_clear_active_tasks_removes_all(fake_redis, monkeypatch):
    """clear_active_tasks удаляет все задачи из реестра и Redis."""
    from domains.generation.registries import task_registry

    monkeypatch.setattr(task_registry, "redis_client", fake_redis)

    tasks = []
    for user_id in [10, 20, 30]:
        task = asyncio.create_task(asyncio.sleep(0))
        tasks.append(task)
        await register_active_task(uid=user_id, task=task)

    # Проверяем, что uid записаны в Redis
    assert await fake_redis.sismember("bot:active_tasks", 10)
    assert await fake_redis.sismember("bot:active_tasks", 20)
    assert await fake_redis.sismember("bot:active_tasks", 30)

    result = await clear_active_tasks()

    # Все задачи должны быть удалены из памяти
    assert get_active_task(uid=10) is None
    assert get_active_task(uid=20) is None
    assert get_active_task(uid=30) is None

    # Redis-множество должно быть очищено
    assert not await fake_redis.sismember("bot:active_tasks", 10)
    assert not await fake_redis.sismember("bot:active_tasks", 20)
    assert not await fake_redis.sismember("bot:active_tasks", 30)

    # Проверяем результат
    assert result["deleted_set"] == 1
    assert result["skipped"] is False

    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
