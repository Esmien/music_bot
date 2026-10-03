"""Юнит-тесты ownership-safe cleanup реестра активных задач.

Проверяем, что каждый экземпляр бота работает только со своим набором
активных задач в Redis и не трогает задачи других экземпляров.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from domains.generation.registries.task_registry import (
    clear_active_tasks,
    get_active_task,
    register_active_task,
    unregister_active_task,
)


@pytest.fixture
def mock_instances():
    """Создаёт два мок-экземпляра бота для тестирования overlapping startup."""
    from types import SimpleNamespace

    instance_a = SimpleNamespace(instance_id="test-instance-a")
    instance_b = SimpleNamespace(instance_id="test-instance-b")
    return instance_a, instance_b


@pytest.fixture
async def fake_redis_sets():
    """Фейковое хранилище Redis Sets для тестов."""
    storage: dict[str, set[int]] = {}

    async def sadd(key: str, value: int) -> int:
        if key not in storage:
            storage[key] = set()
        storage[key].add(value)
        return 1

    async def srem(key: str, value: int) -> int:
        if key in storage and value in storage[key]:
            storage[key].remove(value)
            return 1
        return 0

    async def delete(key: str) -> int:
        if key in storage:
            del storage[key]
            return 1
        return 0

    async def smembers(key: str) -> set[int]:
        return storage.get(key, set())

    redis_mock = AsyncMock()
    redis_mock.sadd = sadd
    redis_mock.srem = srem
    redis_mock.delete = delete
    redis_mock.smembers = smembers

    return redis_mock, storage


async def test_each_instance_uses_own_redis_key(mock_instances, fake_redis_sets):
    """Каждый экземпляр регистрирует задачи в своём Redis-ключе."""
    instance_a, instance_b = mock_instances
    redis_mock, storage = fake_redis_sets

    with patch("domains.generation.registries.task_registry.redis_client", redis_mock):
        # Instance A регистрирует задачу пользователя 1
        with patch("domains.generation.registries.task_registry.current_instance", instance_a):
            task_a = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=1, task=task_a)

        # Instance B регистрирует задачу пользователя 2
        with patch("domains.generation.registries.task_registry.current_instance", instance_b):
            task_b = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=2, task=task_b)

        # Проверяем, что каждый набор содержит только своего пользователя
        assert storage["bot:active_tasks:test-instance-a"] == {1}
        assert storage["bot:active_tasks:test-instance-b"] == {2}

        task_a.cancel()
        task_b.cancel()
        await asyncio.gather(task_a, task_b, return_exceptions=True)


async def test_cleanup_removes_only_own_tasks(mock_instances, fake_redis_sets):
    """cleanup удаляет только задачи текущего экземпляра."""
    instance_a, instance_b = mock_instances
    redis_mock, storage = fake_redis_sets

    with patch("domains.generation.registries.task_registry.redis_client", redis_mock):
        # Instance A регистрирует задачу пользователя 1
        with patch("domains.generation.registries.task_registry.current_instance", instance_a):
            task_a = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=1, task=task_a)

        # Instance B регистрирует задачу пользователя 2
        with patch("domains.generation.registries.task_registry.current_instance", instance_b):
            task_b = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=2, task=task_b)

        # Instance A выполняет cleanup
        with patch("domains.generation.registries.task_registry.current_instance", instance_a):
            result = await clear_active_tasks()

        # Набор instance A удалён
        assert "bot:active_tasks:test-instance-a" not in storage
        # Набор instance B не тронут
        assert storage["bot:active_tasks:test-instance-b"] == {2}
        # Результат cleanup корректен
        assert result["deleted_count"] == 1

        task_a.cancel()
        task_b.cancel()
        await asyncio.gather(task_a, task_b, return_exceptions=True)


async def test_overlapping_startup_does_not_conflict(mock_instances, fake_redis_sets):
    """Два экземпляра, стартующих одновременно, не конфликтуют."""
    instance_a, instance_b = mock_instances
    redis_mock, storage = fake_redis_sets

    with patch("domains.generation.registries.task_registry.redis_client", redis_mock):
        # Оба экземпляра выполняют cleanup при старте почти одновременно
        with patch("domains.generation.registries.task_registry.current_instance", instance_a):
            result_a = await clear_active_tasks()

        with patch("domains.generation.registries.task_registry.current_instance", instance_b):
            result_b = await clear_active_tasks()

        # Оба cleanup прошли успешно
        assert result_a["deleted_count"] == 0
        assert result_b["deleted_count"] == 0

        # Теперь каждый регистрирует задачу
        with patch("domains.generation.registries.task_registry.current_instance", instance_a):
            task_a = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=1, task=task_a)

        with patch("domains.generation.registries.task_registry.current_instance", instance_b):
            task_b = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=2, task=task_b)

        # Оба набора существуют независимо
        assert storage["bot:active_tasks:test-instance-a"] == {1}
        assert storage["bot:active_tasks:test-instance-b"] == {2}

        task_a.cancel()
        task_b.cancel()
        await asyncio.gather(task_a, task_b, return_exceptions=True)


async def test_unregister_removes_from_correct_instance(mock_instances, fake_redis_sets):
    """unregister удаляет задачу из набора текущего экземпляра."""
    instance_a, instance_b = mock_instances
    redis_mock, storage = fake_redis_sets

    with patch("domains.generation.registries.task_registry.redis_client", redis_mock):
        # Instance A регистрирует задачу
        with patch("domains.generation.registries.task_registry.current_instance", instance_a):
            task_a = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=1, task=task_a)

        # Instance B регистрирует задачу
        with patch("domains.generation.registries.task_registry.current_instance", instance_b):
            task_b = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=2, task=task_b)

        # Instance A снимает регистрацию своей задачи
        with patch("domains.generation.registries.task_registry.current_instance", instance_a):
            await unregister_active_task(uid=1, task=task_a)

        # Набор instance A пуст
        assert await redis_mock.smembers("bot:active_tasks:test-instance-a") == set()
        # Набор instance B не тронут
        assert storage["bot:active_tasks:test-instance-b"] == {2}

        # Проверяем in-memory состояние
        assert get_active_task(uid=1) is None

        task_a.cancel()
        task_b.cancel()
        await asyncio.gather(task_a, task_b, return_exceptions=True)


async def test_stale_instance_cleanup_does_not_affect_active(mock_instances, fake_redis_sets):
    """Cleanup старого экземпляра не влияет на активный экземпляр."""
    instance_a, instance_b = mock_instances
    redis_mock, storage = fake_redis_sets

    with patch("domains.generation.registries.task_registry.redis_client", redis_mock):
        # Instance A создал задачу и упал (оставил ключ в Redis)
        storage["bot:active_tasks:test-instance-a"] = {1}

        # Instance B стартовал и работает
        with patch("domains.generation.registries.task_registry.current_instance", instance_b):
            task_b = asyncio.create_task(asyncio.sleep(0))
            await register_active_task(uid=2, task=task_b)

        # Новый Instance A стартует и выполняет cleanup своего старого ключа
        with patch("domains.generation.registries.task_registry.current_instance", instance_a):
            result = await clear_active_tasks()

        # Instance A очистил свой старый ключ
        assert "bot:active_tasks:test-instance-a" not in storage
        assert result["deleted_count"] == 1

        # Instance B не тронут
        assert storage["bot:active_tasks:test-instance-b"] == {2}

        task_b.cancel()
        await asyncio.gather(task_b, return_exceptions=True)
