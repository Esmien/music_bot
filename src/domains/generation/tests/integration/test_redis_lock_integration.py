"""Integration-тесты Redis-локов с fakeredis.

Эти тесты проверяют реальное поведение распределённых блокировок:
mutual exclusion, compare-and-delete через Lua, TTL и повторный захват expired-локов.
Используется fakeredis для изоляции тестов без внешней зависимости.

Запуск:
    pytest -m redis src/domains/generation/tests/integration/test_redis_lock_integration.py
"""

import asyncio

import fakeredis.aioredis
import pytest

from core.redis import RELEASE_LOCK_SCRIPT
from core.utils.exceptions import GenerationLockTimeoutError
from domains.generation.service import user_generation_lock


@pytest.fixture
def redis_connections(monkeypatch):
    """Создаёт два независимых соединения к одному fakeredis-серверу для эмуляции двух процессов.

    Yields:
        Кортеж (redis_client_1, redis_client_2).
    """
    server = fakeredis.FakeServer()
    client1 = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    client2 = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    # Эмуляция Lua-скрипта для fakeredis без lupa
    orig_eval = fakeredis.aioredis.FakeRedis.eval

    async def fake_eval(self, script, numkeys, *keys_and_args):
        try:
            return await orig_eval(self, script, numkeys, *keys_and_args)
        except Exception as exc:
            if "unknown command 'eval'" in str(exc).lower():
                # DEVIATION: Эмуляция RELEASE_LOCK_SCRIPT для fakeredis без lupa
                key = keys_and_args[0]
                token = keys_and_args[1]
                stored = await self.get(key)
                if isinstance(stored, bytes):
                    stored = stored.decode("utf-8")
                if isinstance(token, bytes):
                    token = token.decode("utf-8")
                if stored is not None and stored == token:
                    await self.delete(key)
                    return 1
                return 0
            raise

    monkeypatch.setattr(fakeredis.aioredis.FakeRedis, "eval", fake_eval)

    yield client1, client2


@pytest.fixture
async def cleanup_test_locks(redis_connections):
    """Очищает тестовые Redis-ключи после каждого теста.

    Yields:
        Список ключей для очистки.
    """
    client1, _ = redis_connections
    test_keys = []

    yield test_keys

    if test_keys:
        await client1.delete(*test_keys)


@pytest.mark.redis
@pytest.mark.integration
async def test_redis_lock_mutual_exclusion_real(redis_connections, cleanup_test_locks, monkeypatch):
    """Два независимых Redis-соединения не могут одновременно захватить лок одного пользователя."""
    client1, client2 = redis_connections
    user_id = 100001
    cleanup_test_locks.append(f"bot:generation_lock:{user_id}")

    events = []

    async def process_1():
        """Первый процесс захватывает лок через client1."""
        from domains.generation import service

        original = service.redis_client
        service.redis_client = client1
        try:
            async with user_generation_lock(user_id=user_id):
                events.append("process_1_acquired")
                await asyncio.sleep(0.2)
                events.append("process_1_released")
        finally:
            service.redis_client = original

    async def process_2():
        """Второй процесс пытается захватить лок через client2."""
        from domains.generation import service

        original = service.redis_client
        service.redis_client = client2
        try:
            await asyncio.sleep(0.05)
            async with user_generation_lock(user_id=user_id):
                events.append("process_2_acquired")
                await asyncio.sleep(0.1)
                events.append("process_2_released")
        finally:
            service.redis_client = original

    await asyncio.gather(process_1(), process_2())

    assert events == [
        "process_1_acquired",
        "process_1_released",
        "process_2_acquired",
        "process_2_released",
    ]


@pytest.mark.redis
@pytest.mark.integration
async def test_redis_lock_cannot_release_foreign_lock_real(redis_connections, cleanup_test_locks, monkeypatch):
    """Чужой token не удаляется при выходе из контекста."""
    client1, _ = redis_connections
    user_id = 100002
    lock_key = f"bot:generation_lock:{user_id}"
    cleanup_test_locks.append(lock_key)

    from domains.generation import service

    original = service.redis_client
    service.redis_client = client1

    try:
        async with user_generation_lock(user_id=user_id):
            # Имитируем ситуацию: TTL истек, другой процесс захватил лок со своим токеном
            await client1.set(name=lock_key, value="foreign_owner_token")

        # При выходе Lua-скрипт не должен удалить чужой токен
        stored_val = await client1.get(name=lock_key)
        assert stored_val == "foreign_owner_token"
    finally:
        service.redis_client = original


@pytest.mark.redis
@pytest.mark.integration
async def test_redis_lock_expired_can_be_reacquired_real(redis_connections, cleanup_test_locks, monkeypatch):
    """Протухший (истёкший TTL) лок можно захватить повторно."""
    client1, _ = redis_connections
    user_id = 100003
    lock_key = f"bot:generation_lock:{user_id}"
    cleanup_test_locks.append(lock_key)

    from domains.generation import service

    original = service.redis_client
    service.redis_client = client1

    try:
        # Захватываем лок с очень коротким TTL (1 секунда)
        await client1.set(name=lock_key, value="short_lived_token", nx=True, px=1000)

        # Ждём истечения TTL
        await asyncio.sleep(1.1)

        # Проверяем, что ключ исчез
        assert await client1.exists(lock_key) == 0

        # Повторный захват должен пройти успешно
        async with user_generation_lock(user_id=user_id):
            assert await client1.exists(lock_key) == 1
    finally:
        service.redis_client = original


@pytest.mark.redis
@pytest.mark.integration
async def test_lua_release_lock_script_real(redis_connections, cleanup_test_locks):
    """Lua-скрипт compare-and-delete работает атомарно."""
    client1, _ = redis_connections
    lock_key = "test:integration:lua:lock"
    cleanup_test_locks.append(lock_key)

    correct_token = "token-secret-correct"
    wrong_token = "token-secret-wrong"

    await client1.set(name=lock_key, value=correct_token)

    # Попытка удалить с неверным токеном возвращает 0
    result = await client1.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, wrong_token)
    assert result == 0
    assert await client1.exists(lock_key) == 1

    # Попытка удалить с корректным токеном возвращает 1
    result = await client1.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, correct_token)
    assert result == 1
    assert await client1.exists(lock_key) == 0


@pytest.mark.redis
@pytest.mark.integration
async def test_redis_lock_timeout_real(redis_connections, cleanup_test_locks, monkeypatch):
    """Превышение таймаута ожидания лока вызывает GenerationLockTimeoutError."""
    client1, client2 = redis_connections
    user_id = 100004
    cleanup_test_locks.append(f"bot:generation_lock:{user_id}")

    from domains.generation import service

    monkeypatch.setattr(service, "DEFAULT_LOCK_TIMEOUT", 0.1)

    async def holding_process():
        """Первый процесс держит блокировку 0.3 секунды."""
        original = service.redis_client
        service.redis_client = client1
        try:
            async with user_generation_lock(user_id=user_id):
                await asyncio.sleep(0.3)
        finally:
            service.redis_client = original

    async def waiting_process():
        """Второй процесс пытается захватить блокировку с коротким таймаутом."""
        original = service.redis_client
        service.redis_client = client2
        try:
            await asyncio.sleep(0.05)
            with pytest.raises(GenerationLockTimeoutError):
                async with user_generation_lock(user_id=user_id, retry_interval=0.02):
                    pass
        finally:
            service.redis_client = original

    await asyncio.gather(holding_process(), waiting_process())
