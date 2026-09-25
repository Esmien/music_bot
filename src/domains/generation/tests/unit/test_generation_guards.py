"""Юнит-тесты вспомогательных механизмов сервисного конвейера генерации."""

import asyncio

import pytest

from domains.generation.service import _generation_locks, user_generation_lock

pytestmark = pytest.mark.unit


async def test_generation_lock_is_per_user():
    """Один и тот же пользователь захватывает один лок, разные — разные."""
    async with user_generation_lock(user_id=1):
        first = _generation_locks[1]
        async with user_generation_lock(user_id=2):
            assert _generation_locks[2] is not first


async def test_generation_lock_entry_removed_after_release():
    """После выхода из контекста запись пользователя удаляется из словаря."""
    async with user_generation_lock(user_id=42):
        assert 42 in _generation_locks
    assert 42 not in _generation_locks


async def test_generation_lock_is_exclusive():
    """Второй захват того же пользователя ждёт освобождения первого."""
    order = []

    async def worker() -> None:
        async with user_generation_lock(user_id=7):
            order.append("inside")
        order.append("released")

    async with user_generation_lock(user_id=7):
        task = asyncio.create_task(worker())
        await asyncio.sleep(0)  # даём worker дойти до acquire и встать в очередь
        assert order == []  # worker ещё ждёт лок
    await task
    assert order == ["inside", "released"]
