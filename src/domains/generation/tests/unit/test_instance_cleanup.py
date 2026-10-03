"""Юнит-тесты безопасного cleanup при rolling restart и обработки RedisError."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from redis.exceptions import RedisError

from core.instance import BotInstance


@pytest.fixture
def bot_instance():
    """Создаёт новый экземпляр бота для каждого теста."""
    return BotInstance()


async def test_instance_registration(bot_instance):
    """Экземпляр успешно регистрируется в Redis с heartbeat."""
    with patch("core.instance.redis_client") as mock_redis:
        mock_redis.sadd = AsyncMock()
        mock_redis.set = AsyncMock()

        await bot_instance.register()

        # Проверяем регистрацию в множестве и создание heartbeat
        mock_redis.sadd.assert_called_once()
        mock_redis.set.assert_called_once()
        assert bot_instance._heartbeat_task is not None


async def test_instance_unregistration(bot_instance):
    """Экземпляр успешно снимается с регистрации."""
    with patch("core.instance.redis_client") as mock_redis:
        mock_redis.sadd = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.srem = AsyncMock()
        mock_redis.delete = AsyncMock()

        await bot_instance.register()
        await bot_instance.unregister()

        # Проверяем удаление из множества и heartbeat
        mock_redis.srem.assert_called_once()
        mock_redis.delete.assert_called_once()


async def test_has_other_active_instances_true(bot_instance):
    """Обнаружение другого активного экземпляра с живым heartbeat."""
    other_instance_id = "other-instance-id"

    with patch("core.instance.redis_client") as mock_redis:
        # Эмулируем наличие другого экземпляра в множестве
        mock_redis.smembers = AsyncMock(return_value={bot_instance.instance_id, other_instance_id})
        # Heartbeat другого экземпляра существует
        mock_redis.exists = AsyncMock(return_value=1)

        result = await bot_instance.has_other_active_instances()

        assert result is True


async def test_has_other_active_instances_false_no_others(bot_instance):
    """Нет других экземпляров в множестве."""
    with patch("core.instance.redis_client") as mock_redis:
        # Только текущий экземпляр в множестве
        mock_redis.smembers = AsyncMock(return_value={bot_instance.instance_id})

        result = await bot_instance.has_other_active_instances()

        assert result is False


async def test_has_other_active_instances_false_stale_heartbeat(bot_instance):
    """Другие экземпляры есть, но их heartbeat истёк."""
    other_instance_id = "stale-instance-id"

    with patch("core.instance.redis_client") as mock_redis:
        mock_redis.smembers = AsyncMock(return_value={bot_instance.instance_id, other_instance_id})
        # Heartbeat другого экземпляра не существует (истёк)
        mock_redis.exists = AsyncMock(return_value=0)

        result = await bot_instance.has_other_active_instances()

        assert result is False


async def test_cleanup_stale_instances(bot_instance):
    """Удаление stale записей экземпляров без heartbeat."""
    stale_instance_1 = "stale-1"
    stale_instance_2 = "stale-2"
    active_instance = "active-1"

    with patch("core.instance.redis_client") as mock_redis:
        mock_redis.smembers = AsyncMock(
            return_value={bot_instance.instance_id, stale_instance_1, stale_instance_2, active_instance}
        )

        # stale экземпляры не имеют heartbeat, active имеет
        async def exists_side_effect(key):
            if "active-1" in key:
                return 1
            return 0

        mock_redis.exists = AsyncMock(side_effect=exists_side_effect)
        mock_redis.srem = AsyncMock()

        stale_count = await bot_instance.cleanup_stale_instances()

        # Удалили 2 stale записи
        assert stale_count == 2
        assert mock_redis.srem.call_count == 2


async def test_clear_active_tasks_removes_own_instance_tasks():
    """Cleanup удаляет задачи только текущего экземпляра."""
    from types import SimpleNamespace

    from domains.generation.registries.task_registry import clear_active_tasks

    mock_instance = SimpleNamespace(instance_id="test-instance-123")

    with (
        patch("domains.generation.registries.task_registry.redis_client") as mock_redis,
        patch("domains.generation.registries.task_registry.current_instance", mock_instance),
    ):
        mock_redis.delete = AsyncMock(return_value=1)

        result = await clear_active_tasks()

        # Проверяем вызов delete с правильным ключом
        mock_redis.delete.assert_called_once_with("bot:active_tasks:test-instance-123")
        assert result["deleted_count"] == 1
        assert result["memory_cleared"] >= 0


async def test_clear_active_tasks_handles_redis_error():
    """Cleanup корректно обрабатывает RedisError."""
    from types import SimpleNamespace

    from domains.generation.registries.task_registry import clear_active_tasks

    mock_instance = SimpleNamespace(instance_id="test-instance-456")

    with (
        patch("domains.generation.registries.task_registry.redis_client") as mock_redis,
        patch("domains.generation.registries.task_registry.current_instance", mock_instance),
    ):
        mock_redis.delete = AsyncMock(side_effect=RedisError("Connection failed"))

        with pytest.raises(RedisError):
            await clear_active_tasks()


async def test_clear_orphaned_flags_skips_when_other_instances():
    """FSM cleanup пропускается при наличии других активных экземпляров."""
    from domains.generation.fsm import clear_orphaned_generation_flags

    with patch("domains.generation.fsm.current_instance") as mock_instance:
        mock_instance.has_other_active_instances = AsyncMock(return_value=True)

        result = await clear_orphaned_generation_flags(skip_if_other_instances=True)

        assert result["skipped"] is True
        assert result["cleared"] == 0


async def test_clear_orphaned_flags_handles_redis_error():
    """FSM cleanup корректно обрабатывает RedisError."""
    from domains.generation.fsm import clear_orphaned_generation_flags

    with patch("domains.generation.fsm.current_instance") as mock_instance:
        mock_instance.has_other_active_instances = AsyncMock(side_effect=RedisError("Connection failed"))

        with pytest.raises(RedisError):
            await clear_orphaned_generation_flags(skip_if_other_instances=True)


async def test_concurrent_startup_no_conflict():
    """Два экземпляра стартуют конкурентно без конфликта."""
    instance1 = BotInstance()
    instance2 = BotInstance()

    # Эмулируем параллельный startup двух экземпляров
    with patch("core.instance.redis_client") as mock_redis:
        mock_redis.sadd = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.smembers = AsyncMock(
            side_effect=[
                {instance1.instance_id},  # Первый запрос от instance1
                {instance1.instance_id, instance2.instance_id},  # Второй запрос от instance2
            ]
        )
        mock_redis.exists = AsyncMock(return_value=1)

        # Регистрируем оба экземпляра параллельно
        await asyncio.gather(instance1.register(), instance2.register())

        # instance2 должен увидеть instance1 как активный
        has_others = await instance2.has_other_active_instances()
        assert has_others is True

        # Cleanup
        mock_redis.srem = AsyncMock()
        mock_redis.delete = AsyncMock()
        await instance1.unregister()
        await instance2.unregister()
