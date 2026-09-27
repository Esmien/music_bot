"""Точка запуска TaskIQ-воркеров приложения."""

import argparse
import asyncio
import logging
import signal

from aiogram import Bot
from aiogram.fsm.storage.redis import RedisStorage
from taskiq import TaskiqEvents, TaskiqState
from taskiq.receiver import Receiver

from core.broker import broker
from core.config import settings
from core.redis import redis_client
from shared.ports.telegram import AiogramTelegramPort

log = logging.getLogger(__name__)


async def run_worker(*, worker_name: str) -> None:
    """Запускает TaskIQ-консьюмер выбранного домена."""

    # Изолируем импорты, чтобы в память грузились только нужные таски
    if worker_name == "enricher":
        import domains.enricher.worker  # noqa
    elif worker_name == "generation":
        import domains.generation.worker  # noqa
    elif worker_name == "evaluation":
        import domains.evaluation.worker  # noqa
    elif worker_name == "credits":
        import domains.credits.worker  # noqa

    broker.is_worker_process = True

    async def on_startup(state: TaskiqState) -> None:
        state["bot"] = Bot(token=settings.bot.BOT_TOKEN)
        state["storage"] = RedisStorage(redis=redis_client)
        state["telegram_port"] = AiogramTelegramPort(
            bot_token=settings.bot.BOT_TOKEN,
            owner_id=settings.bot.BOT_OWNER_ID,
        )

    async def on_shutdown(state: TaskiqState) -> None:
        if bot := state.get("bot"):
            await bot.session.close()
        if storage := state.get("storage"):
            await storage.close()
        if port := state.get("telegram_port"):
            await port.close()

    broker.add_event_handler(TaskiqEvents.WORKER_STARTUP, on_startup)
    broker.add_event_handler(TaskiqEvents.WORKER_SHUTDOWN, on_shutdown)

    await broker.startup()

    # Настраиваем graceful shutdown для перехвата сигналов от Docker
    loop = asyncio.get_running_loop()
    finish_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, finish_event.set)

    try:
        log.info("Worker %s is listening for tasks...", worker_name)
        receiver = Receiver(broker)
        # Передаем event, чтобы воркер корректно остановился при SIGTERM
        await receiver.listen(finish_event=finish_event)
    finally:
        log.info("Shutting down %s worker...", worker_name)
        await broker.shutdown()


def main() -> None:
    """Разбирает аргументы командной строки и запускает выбранный воркер."""
    parser = argparse.ArgumentParser(description="Запуск TaskIQ-консьюмера")
    parser.add_argument("worker", choices=("credits", "enricher", "evaluation", "generation"))
    worker_name = parser.parse_args().worker

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    asyncio.run(run_worker(worker_name=worker_name))


if __name__ == "__main__":
    main()
