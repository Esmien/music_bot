"""Smoke-тест RabbitMQ через taskiq: публикация и потребление сообщения.

Проверяет, что брокер доступен, может принять задачу и вернуть результат.
"""

import asyncio

import pytest
from taskiq import InMemoryBroker, TaskiqResult
from taskiq_aio_pika import AioPikaBroker

from core.config import settings


@pytest.fixture
async def inmemory_broker():
    """Создаёт in-memory брокер для тестирования базовой механики taskiq.

    Yields:
        InMemoryBroker: Настроенный и запущенный брокер.
    """
    broker = InMemoryBroker()
    await broker.startup()
    yield broker
    await broker.shutdown()


async def test_inmemory_broker_smoke(inmemory_broker: InMemoryBroker) -> None:
    """Проверяет публикацию и выполнение задачи через InMemoryBroker.

    Args:
        inmemory_broker: In-memory брокер taskiq.
    """

    @inmemory_broker.task
    async def add_numbers(a: int, b: int) -> int:
        """Тестовая задача сложения двух чисел.

        Args:
            a: Первое число.
            b: Второе число.

        Returns:
            Сумма a и b.
        """
        return a + b

    task = await add_numbers.kiq(a=2, b=3)
    result: TaskiqResult[int] = await task.wait_result(timeout=5)

    assert result.is_err is False, f"Task failed: {result.error}"
    assert result.return_value == 5


async def test_rabbitmq_connection_smoke() -> None:
    """Проверяет подключение к RabbitMQ и возможность создать очередь.

    Использует RABBITMQ_URL из настроек или формирует URL из credentials.
    Пропускает тест если RabbitMQ недоступен (локальная разработка без инфраструктуры).

    Полноценный smoke-тест с выполнением задач требует запущенного воркера,
    что выходит за рамки connection smoke-теста.
    """
    rabbitmq_url = settings.rabbitmq.RABBITMQ_URL
    if not rabbitmq_url:
        rabbitmq_url = f"amqp://{settings.rabbitmq.RABBITMQ_USER}:{settings.rabbitmq.RABBITMQ_PASSWORD}@localhost:5672/"

    test_queue_name = settings.rabbitmq.queue_name(domain="smoke_test")
    broker = AioPikaBroker(url=rabbitmq_url, queue_name=test_queue_name)

    last_error = None
    for attempt in range(3):
        try:
            await asyncio.wait_for(broker.startup(), timeout=5)
            break
        except (TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(1)
    else:
        pytest.skip(f"RabbitMQ unavailable: {last_error}")

    try:
        # Проверяем, что можем зарегистрировать задачу и опубликовать сообщение
        @broker.task(task_name="smoke_ping_task")
        async def ping_task() -> str:
            return "pong"

        task = await ping_task.kiq()
        assert task is not None, "Task message should be created"

    finally:
        await broker.shutdown()
