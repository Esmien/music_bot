"""Общий конфигуратор брокера TaskIQ на базе RabbitMQ."""

from taskiq import InMemoryBroker
from taskiq.serializers import JSONSerializer
from taskiq_aio_pika import AioPikaBroker

from core.config import settings

# Единый брокер для всех доменов
broker = AioPikaBroker(
    url=settings.rabbitmq.RABBITMQ_URL,
    exchange_name="songai_events",
    prefetch_count=settings.rabbitmq.RABBITMQ_PREFETCH,
    # Общая Dead Letter Queue (DLQ) для всех невыполненных/упавших задач
    queue_arguments={
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": "songai_dead_letters",
    },
).with_serializer(JSONSerializer())


def _create_inmemory_broker() -> InMemoryBroker:
    """Создаёт in-memory брокер для тестов."""
    return InMemoryBroker().with_serializer(JSONSerializer())


# Временные алиасы, чтобы не переписывать импорты прямо сейчас во всех воркерах.
# Позже, можно будет заменить везде на `from core.broker import broker`.
enricher_broker = broker
generation_broker = broker
evaluation_broker = broker
feedback_broker = broker
credits_broker = broker

# Словарь brokers теперь тоже ссылается на один инстанс (нужен для запуска .startup() в bot.py)
brokers = {"main": broker}
