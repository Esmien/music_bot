"""Общий конфигуратор брокеров TaskIQ на базе RabbitMQ."""

from taskiq import InMemoryBroker
from taskiq.serializers import JSONSerializer
from taskiq_aio_pika import AioPikaBroker

from core.config import settings

_QUEUE_DOMAINS = ("enricher", "generation", "credits")


def _create_broker(domain: str) -> AioPikaBroker:
    """Создаёт брокер TaskIQ для очереди домена.

    Args:
        domain: Имя домена, которому принадлежит очередь.

    Returns:
        Настроенный брокер с JSON-сериализацией и DLQ.

    Raises:
        ValueError: Если передан неизвестный домен.
    """
    if domain not in _QUEUE_DOMAINS:
        raise ValueError(f"Unsupported broker domain: {domain}")

    queue_name = settings.rabbitmq.queue_name(domain)
    dead_letter_queue = settings.rabbitmq.dead_letter_queue_name(domain)

    return AioPikaBroker(
        url=settings.rabbitmq.RABBITMQ_URL,
        queue_name=queue_name,
        prefetch_count=settings.rabbitmq.RABBITMQ_PREFETCH,
        queue_arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": dead_letter_queue,
        },
    ).with_serializer(JSONSerializer())


def _create_inmemory_broker() -> InMemoryBroker:
    """Создаёт in-memory брокер для тестов.

    Задачи выполняются синхронно в процессе теста без реального брокера.

    Returns:
        InMemoryBroker с JSON-сериализацией.
    """
    return InMemoryBroker().with_serializer(JSONSerializer())


enricher_broker = _create_broker("enricher")
generation_broker = _create_broker("generation")
credits_broker = _create_broker("credits")

# Главный брокер для credits домена
broker = credits_broker

brokers = {
    "enricher": enricher_broker,
    "generation": generation_broker,
    "credits": credits_broker,
}
