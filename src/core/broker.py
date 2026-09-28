"""Общий конфигуратор брокеров TaskIQ на базе RabbitMQ."""

from taskiq import InMemoryBroker
from taskiq.serializers import JSONSerializer
from taskiq_aio_pika import AioPikaBroker

from core.config import settings


def _create_broker(domain: str) -> AioPikaBroker:
    """Создаёт брокер TaskIQ для полностью изолированной очереди домена."""
    return AioPikaBroker(
        url=settings.rabbitmq.RABBITMQ_URL,
        exchange_name=f"songai_{domain}_exchange",  # У каждого домена свой обменник!
        queue_name=settings.rabbitmq.queue_name(domain),
        prefetch_count=settings.rabbitmq.RABBITMQ_PREFETCH,
        queue_arguments={
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": "songai_dead_letters",
        },
    ).with_serializer(JSONSerializer())


def _create_inmemory_broker() -> InMemoryBroker:
    return InMemoryBroker().with_serializer(JSONSerializer())


enricher_broker = _create_broker("enricher")
generation_broker = _create_broker("generation")
evaluation_broker = _create_broker("evaluation")
feedback_broker = _create_broker("feedback")
credits_broker = _create_broker("credits")

# Реестр для точки запуска
brokers = {
    "enricher": enricher_broker,
    "generation": generation_broker,
    "evaluation": evaluation_broker,
    "feedback": feedback_broker,
    "credits": credits_broker,
}
