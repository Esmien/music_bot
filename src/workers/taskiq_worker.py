"""Точка запуска TaskIQ-воркеров приложения."""

import argparse
import asyncio
import logging

from taskiq import TaskiqEvents, TaskiqState
from taskiq.receiver import Receiver

from core.config import settings
from shared.ports.telegram import AiogramTelegramPort

log = logging.getLogger(__name__)


async def run_worker(*, worker_name: str) -> None:
    """Запускает TaskIQ-консьюмер выбранного домена.

    Args:
        worker_name: Имя домена, очередь которого будет обрабатываться.
    """
    from core.broker import credits_broker, enricher_broker, generation_broker

    _local_brokers = {
        "credits": credits_broker,
        "enricher": enricher_broker,
        "generation": generation_broker,
    }

    broker = _local_brokers[worker_name]
    broker.is_worker_process = True

    if worker_name == "enricher":
        from domains.enricher import worker as _enricher_worker  # noqa: F401
    elif worker_name == "generation":
        from domains.generation import worker as _generation_worker  # noqa: F401
    elif worker_name == "credits":
        from domains.credits import worker as _credits_worker  # noqa: F401

    async def on_startup(state: TaskiqState) -> None:
        state["telegram_port"] = AiogramTelegramPort(
            bot_token=settings.bot.BOT_TOKEN,
            owner_id=settings.bot.BOT_OWNER_ID,
        )

    async def on_shutdown(state: TaskiqState) -> None:
        telegram_port = state.get("telegram_port")
        if telegram_port is not None:
            await telegram_port.close()
        state.clear()

    # Регистрируем event handlers до startup, чтобы они сработали
    broker.add_event_handler(TaskiqEvents.WORKER_STARTUP, on_startup)
    broker.add_event_handler(TaskiqEvents.WORKER_SHUTDOWN, on_shutdown)

    # Запускаем брокер
    log.info("Starting %s worker broker", worker_name)
    await broker.startup()

    try:
        log.info("Worker %s is listening for tasks", worker_name)
        receiver = Receiver(broker)
        await receiver.listen(finish_event=asyncio.Event())
    finally:
        log.info("Shutting down %s worker", worker_name)
        await broker.shutdown()


def main() -> None:
    """Разбирает аргументы командной строки и запускает выбранный воркер."""
    parser = argparse.ArgumentParser(description="Запуск TaskIQ-консьюмера")
    parser.add_argument("worker", choices=("credits", "enricher", "generation"))
    worker_name = parser.parse_args().worker

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    asyncio.run(run_worker(worker_name=worker_name))


if __name__ == "__main__":
    main()
