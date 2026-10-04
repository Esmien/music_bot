"""Integration-тесты распределённого Redis-лока с настоящим Redis-сервером.

Эти тесты проверяют реальное поведение Redis без монкипатчей и fakeredis.
Требуют запущенного Redis-сервера (docker-compose up redis-test).

Запуск:
    pytest -v -m redis_real src/domains/generation/tests/integration/test_redis_lock_real.py
"""

import asyncio
import os

import pytest
import redis.asyncio as aioredis

from core.redis import RELEASE_LOCK_SCRIPT
from core.utils.exceptions import GenerationLockTimeoutError
from domains.generation.service import user_generation_lock


def get_redis_url() -> str:
    """Получить URL настоящего Redis для тестов."""
    return os.getenv("REDIS_TEST_URL", "redis://localhost:6379/15")


@pytest.fixture
async def real_redis_connections():
    """Создаёт два независимых соединения к настоящему Redis-серверу.

    Returns:
        Кортеж (redis_client_1, redis_client_2).

    Raises:
        pytest.skip: Если Redis недоступен.
    """
    redis_url = get_redis_url()

    try:
        client1 = aioredis.from_url(url=redis_url, decode_responses=False)
        client2 = aioredis.from_url(url=redis_url, decode_responses=False)

        # Проверяем доступность
        await client1.ping()
        await client2.ping()
    except Exception as exc:
        pytest.skip(f"Redis server not available at {redis_url}: {exc}")

    # Очищаем тестовую БД перед тестами
    await client1.flushdb()

    yield client1, client2

    # Очищаем после тестов
    await client1.flushdb()
    await client1.aclose()
    await client2.aclose()


@pytest.mark.redis_real
@pytest.mark.integration
async def test_real_redis_lock_mutual_exclusion(real_redis_connections, monkeypatch):
    """Два процесса не могут одновременно захватить лок одного пользователя через настоящий Redis."""
    client1, client2 = real_redis_connections

    from domains.generation import service

    user_id = 123
    events = []

    async def process_1():
        """Первый процесс захватывает лок, работает 0.2 сек, освобождает."""
        monkeypatch.setattr(service, "redis_client", client1)
        async with user_generation_lock(user_id=user_id):
            events.append("process_1_acquired")
            await asyncio.sleep(0.2)
            events.append("process_1_released")

    async def process_2():
        """Второй процесс пытается захватить лок через другое соединение."""
        monkeypatch.setattr(service, "redis_client", client2)
        await asyncio.sleep(0.05)  # Даём process_1 время захватить первым
        async with user_generation_lock(user_id=user_id):
            events.append("process_2_acquired")
            await asyncio.sleep(0.1)
            events.append("process_2_released")

    await asyncio.gather(process_1(), process_2())

    # process_2 должен дождаться освобождения лока process_1
    assert events == [
        "process_1_acquired",
        "process_1_released",
        "process_2_acquired",
        "process_2_released",
    ]


@pytest.mark.redis_real
@pytest.mark.integration
async def test_real_redis_lock_different_users_no_blocking(real_redis_connections, monkeypatch):
    """Локи разных пользователей не блокируют друг друга в настоящем Redis."""
    client1, client2 = real_redis_connections

    from domains.generation import service

    events = []

    async def process_1():
        """Первый процесс захватывает лок user_id=100."""
        monkeypatch.setattr(service, "redis_client", client1)
        async with user_generation_lock(user_id=100):
            events.append("user_100_acquired")
            await asyncio.sleep(0.2)
            events.append("user_100_released")

    async def process_2():
        """Второй процесс захватывает лок user_id=200 через другое соединение."""
        monkeypatch.setattr(service, "redis_client", client2)
        await asyncio.sleep(0.05)
        async with user_generation_lock(user_id=200):
            events.append("user_200_acquired")
            await asyncio.sleep(0.1)
            events.append("user_200_released")

    await asyncio.gather(process_1(), process_2())

    # Оба процесса захватывают локи одновременно
    assert events[:2] == ["user_100_acquired", "user_200_acquired"]
    assert set(events[2:]) == {"user_200_released", "user_100_released"}
    assert len(events) == 4


@pytest.mark.redis_real
@pytest.mark.integration
async def test_real_redis_lock_timeout_raises_exception(real_redis_connections, monkeypatch):
    """Превышение таймаута ожидания лока вызывает GenerationLockTimeoutError в настоящем Redis."""
    client1, client2 = real_redis_connections

    from domains.generation import service

    user_id = 456
    monkeypatch.setattr(service, "DEFAULT_LOCK_TIMEOUT", 0.1)

    async def holding_process():
        """Первый процесс держит блокировку 0.3 секунды."""
        monkeypatch.setattr(service, "redis_client", client1)
        async with user_generation_lock(user_id=user_id):
            await asyncio.sleep(0.3)

    async def waiting_process():
        """Второй процесс пытается захватить блокировку с коротким таймаутом."""
        monkeypatch.setattr(service, "redis_client", client2)
        await asyncio.sleep(0.05)
        with pytest.raises(GenerationLockTimeoutError):
            async with user_generation_lock(user_id=user_id, retry_interval=0.02):
                pass

    await asyncio.gather(holding_process(), waiting_process())


@pytest.mark.redis_real
@pytest.mark.integration
async def test_real_redis_lua_script_compare_and_delete(real_redis_connections):
    """Lua-скрипт атомарно удаляет ключ только при совпадении токена в настоящем Redis."""
    client1, _ = real_redis_connections
    lock_key = "test:real:lua:lock"
    correct_token = "token-secret-1"
    wrong_token = "token-secret-2"

    await client1.set(name=lock_key, value=correct_token)

    # Попытка удалить с неверным токеном возвращает 0 и не удаляет ключ
    result = await client1.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, wrong_token)
    assert result == 0
    assert await client1.exists(lock_key) == 1

    # Попытка удалить с корректным токеном возвращает 1 и удаляет ключ
    result = await client1.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, correct_token)
    assert result == 1
    assert await client1.exists(lock_key) == 0


@pytest.mark.redis_real
@pytest.mark.integration
async def test_real_redis_lock_expiration_and_reacquire(real_redis_connections, monkeypatch):
    """Expired lock захватывается повторно в настоящем Redis."""
    client1, client2 = real_redis_connections

    from domains.generation import service

    user_id = 789
    lock_key = f"bot:generation_lock:{user_id}"

    # Процесс 1 захватывает лок с коротким TTL
    monkeypatch.setattr(service, "redis_client", client1)
    async with user_generation_lock(user_id=user_id):
        # Проверяем, что лок существует
        assert await client1.exists(lock_key) == 1

        # Принудительно удаляем лок (имитируем истечение TTL)
        await client1.delete(lock_key)

    # Проверяем, что лок удалён
    assert await client1.exists(lock_key) == 0

    # Процесс 2 может захватить лок
    monkeypatch.setattr(service, "redis_client", client2)
    acquired = False
    async with user_generation_lock(user_id=user_id):
        acquired = True
        assert await client2.exists(lock_key) == 1

    assert acquired is True
    assert await client2.exists(lock_key) == 0


@pytest.mark.redis_real
@pytest.mark.integration
async def test_real_redis_set_nx_px_behavior(real_redis_connections):
    """Проверка настоящего SET NX PX в Redis."""
    client1, client2 = real_redis_connections
    test_key = "test:set_nx_px"
    value1 = "value1"
    value2 = "value2"

    # Первое SET NX PX должно успешно установить ключ
    result1 = await client1.set(name=test_key, value=value1, nx=True, px=5000)
    assert result1 is True
    stored = await client1.get(name=test_key)
    assert stored.decode("utf-8") == value1

    # Второе SET NX PX должно вернуть None (ключ уже существует)
    result2 = await client2.set(name=test_key, value=value2, nx=True, px=5000)
    assert result2 is None
    stored = await client1.get(name=test_key)
    assert stored.decode("utf-8") == value1  # Значение не изменилось

    # Удаляем ключ
    await client1.delete(test_key)

    # Теперь SET NX PX снова успешно установит ключ
    result3 = await client2.set(name=test_key, value=value2, nx=True, px=5000)
    assert result3 is True
    stored = await client2.get(name=test_key)
    assert stored.decode("utf-8") == value2

    await client2.delete(test_key)
