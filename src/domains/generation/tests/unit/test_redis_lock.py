"""Тесты распределённого Redis-лока для генерации.

Проверяем взаимное исключение между двумя «процессами» (два соединения к fakeredis).
"""

import asyncio

import fakeredis.aioredis
import pytest

from domains.generation.service import user_generation_lock


@pytest.fixture
def redis_connections(monkeypatch):
    """Создаёт два независимых соединения к одному fakeredis-серверу.

    Returns:
        Кортеж (redis_client_1, redis_client_2).
    """
    server = fakeredis.FakeServer()
    client1 = fakeredis.aioredis.FakeRedis(server=server, decode_responses=False)
    client2 = fakeredis.aioredis.FakeRedis(server=server, decode_responses=False)

    # Подменяем redis_client в service.py на первый клиент
    from domains.generation import service

    monkeypatch.setattr(service, "redis_client", client1)

    yield client1, client2


async def test_redis_lock_mutual_exclusion(redis_connections):
    """Два «процесса» не могут одновременно захватить лок одного пользователя."""
    client1, client2 = redis_connections

    user_id = 123
    events = []

    async def process_1():
        """Первый процесс захватывает лок, работает 0.2 сек, освобождает."""
        async with user_generation_lock(user_id=user_id):
            events.append("process_1_acquired")
            await asyncio.sleep(0.2)
            events.append("process_1_released")

    async def process_2():
        """Второй процесс пытается захватить лок через redis_client_2."""
        # Временно подменяем redis_client на client2 внутри контекста
        from domains.generation import service

        original = service.redis_client
        service.redis_client = client2
        try:
            # Даём process_1 время захватить лок первым
            await asyncio.sleep(0.05)
            async with user_generation_lock(user_id=user_id):
                events.append("process_2_acquired")
                await asyncio.sleep(0.1)
                events.append("process_2_released")
        finally:
            service.redis_client = original

    # Запускаем оба процесса параллельно
    await asyncio.gather(process_1(), process_2())

    # process_2 должен дождаться освобождения лока process_1
    assert events == [
        "process_1_acquired",
        "process_1_released",
        "process_2_acquired",
        "process_2_released",
    ]


async def test_redis_lock_different_users_no_blocking(redis_connections):
    """Локи разных пользователей не блокируют друг друга."""
    client1, client2 = redis_connections

    events = []

    async def process_1():
        """Первый процесс захватывает лок user_id=100."""
        async with user_generation_lock(user_id=100):
            events.append("user_100_acquired")
            await asyncio.sleep(0.2)
            events.append("user_100_released")

    async def process_2():
        """Второй процесс захватывает лок user_id=200 через другое соединение."""
        from domains.generation import service

        original = service.redis_client
        service.redis_client = client2
        try:
            await asyncio.sleep(0.05)
            async with user_generation_lock(user_id=200):
                events.append("user_200_acquired")
                await asyncio.sleep(0.1)
                events.append("user_200_released")
        finally:
            service.redis_client = original

    await asyncio.gather(process_1(), process_2())

    # process_2 не ждёт process_1: локи независимы
    # Порядок освобождения может варьироваться из-за таймингов asyncio,
    # главное — оба процесса захватили локи одновременно
    assert events[:2] == ["user_100_acquired", "user_200_acquired"]
    assert set(events[2:]) == {"user_200_released", "user_100_released"}
    assert len(events) == 4
