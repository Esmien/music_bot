"""Тестовый in-process runner TaskIQ для интеграционных тестов генерации."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from taskiq import TaskiqEvents, TaskiqState, TaskiqWorker

from core.broker import generation_broker
from shared.ports.fake_telegram import FakeTelegramPort


@asynccontextmanager
async def run_generation_worker() -> AsyncIterator[FakeTelegramPort]:
    """Запускает generation-worker внутри pytest и корректно останавливает его.

    Yields:
        FakeTelegramPort: Порт Telegram, используемый worker-ом в тесте.
    """
    telegram_port = FakeTelegramPort()
    worker = TaskiqWorker(broker=generation_broker)

    async def on_startup(state: TaskiqState) -> None:
        state["telegram_port"] = telegram_port

    async def on_shutdown(state: TaskiqState) -> None:
        state.clear()

    worker.add_event_handler(TaskiqEvents.WORKER_STARTUP, on_startup)
    worker.add_event_handler(TaskiqEvents.WORKER_SHUTDOWN, on_shutdown)

    worker_task = None
    try:
        await worker.startup()
        worker_task = worker.loop.create_task(worker.listen())
        yield telegram_port
    finally:
        if worker_task is not None:
            worker_task.cancel()
            await worker_task
        await worker.shutdown()
