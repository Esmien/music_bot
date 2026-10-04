"""Тесты распределённого Redis-лока для генерации.

Проверяем взаимное исключение между двумя «процессами» (два соединения к fakeredis).

Эти тесты используют fakeredis и предназначены для быстрой проверки логики локов.
Для тестов с настоящим Redis см. test_redis_lock_real.py с маркером @pytest.mark.redis_real.
"""

import asyncio

import fakeredis.aioredis
import pytest

from core.redis import RELEASE_LOCK_SCRIPT
from core.utils.exceptions import GenerationLockTimeoutError
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

    orig_eval = fakeredis.aioredis.FakeRedis.eval

    async def fake_eval(self, script, numkeys, *keys_and_args):
        try:
            return await orig_eval(self, script, numkeys, *keys_and_args)
        except Exception as exc:
            if "unknown command 'eval'" in str(exc).lower():
                # DEVIATION: Эмуляция RELEASE_LOCK_SCRIPT для fakeredis без установленной lupa
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

    # Подменяем redis_client в service.py на первый клиент
    from domains.generation import service

    monkeypatch.setattr(service, "redis_client", client1)

    return client1, client2


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
async def test_redis_lock_timeout_raises_exception(redis_connections, monkeypatch):
    """Превышение таймаута ожидания лока вызывает GenerationLockTimeoutError."""
    _, client2 = redis_connections
    user_id = 456

    from domains.generation import service

    monkeypatch.setattr(service, "DEFAULT_LOCK_TIMEOUT", 0.1)

    async def holding_process():
        """Первый процесс держит блокировку 0.3 секунды."""
        async with user_generation_lock(user_id=user_id):
            await asyncio.sleep(0.3)

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


@pytest.mark.unit
async def test_redis_lock_cannot_release_foreign_lock(redis_connections):
    """Чужой лок не удаляется при выходе из контекста первого процесса."""
    client1, _ = redis_connections
    user_id = 789
    lock_key = f"bot:generation_lock:{user_id}"

    async with user_generation_lock(user_id=user_id):
        # Имитируем ситуацию, когда TTL истек и другой процесс захватил лок со своим токеном
        await client1.set(name=lock_key, value="foreign_owner_token")

    # При выходе из user_generation_lock Lua-скрипт не должен удалить чужой токен
    stored_val = await client1.get(name=lock_key)
    if isinstance(stored_val, bytes):
        stored_val = stored_val.decode("utf-8")
    assert stored_val == "foreign_owner_token"

    await client1.delete(lock_key)


@pytest.mark.unit
async def test_redis_lock_cannot_release_expired_lock(redis_connections):
    """Протухший (удалённый) лок не вызывает ошибок при попытке освобождения."""
    client1, _ = redis_connections
    user_id = 999
    lock_key = f"bot:generation_lock:{user_id}"

    async with user_generation_lock(user_id=user_id):
        # Имитируем истечение TTL: ключ исчезает из Redis
        await client1.delete(lock_key)

    # При выходе из user_generation_lock Lua-скрипт завершается без ошибок
    assert await client1.get(name=lock_key) is None


@pytest.mark.unit
async def test_lua_release_lock_script_direct(redis_connections):
    """Атомарный Lua-скрипт удаляет ключ только при совпадении токена владельца."""
    client1, _ = redis_connections
    lock_key = "test:lua:lock"
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


@pytest.mark.unit
def test_user_lock_event_not_shared_across_loops(monkeypatch):
    """AUD-043: Event для user_id, созданный в одном loop, не переиспользуется в другом.

    Регрессия на RuntimeError 'Event is bound to a different event loop'.
    """
    import asyncio as aio

    import fakeredis.aioredis

    from domains.generation import service

    server = fakeredis.FakeServer()
    client = fakeredis.aioredis.FakeRedis(server=server, decode_responses=False)
    monkeypatch.setattr(service, "redis_client", client)

    async def acquire_and_release(user_id: int) -> None:
        async with service.user_generation_lock(user_id=user_id):
            await aio.sleep(0)

    loop1 = aio.new_event_loop()
    try:
        loop1.run_until_complete(acquire_and_release(user_id=42))
    finally:
        loop1.close()

    loop2 = aio.new_event_loop()
    try:
        loop2.run_until_complete(acquire_and_release(user_id=42))
    finally:
        loop2.close()

    assert service._user_lock_events == {}
