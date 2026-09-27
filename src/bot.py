"""Точка входа Telegram-бота: настройка Bot/Dispatcher и запуск polling."""

import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import ErrorEvent
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from core import router
from core.config import settings
from core.lifecycle import shutdown_all
from core.utils.error_notify import notify_owner
from domains.generation.fsm import clear_orphaned_generation_flags
from domains.generation.registries.task_registry import clear_active_tasks

log = logging.getLogger(__name__)


async def on_error(event: ErrorEvent, bot: Bot) -> bool:
    """Глобальный обработчик непойманных исключений в хендлерах.

    Регистрируется в Dispatcher.errors. Возвращаем True, чтобы aiogram
    считал ошибку обработанной и не пробовал другие обработчики ошибок.

    Args:
        event: Служебный апдейт с подробностями ошибки.
        bot: Экземпляр бота, через который шлём уведомление владельцу.

    Returns:
        Всегда True — ошибка считается обработанной.
    """
    await notify_owner(bot=bot, context=f"Необработанная ошибка: {event.update.update_id}", err=event.exception)
    return True


def create_dispatcher(storage: RedisStorage) -> Dispatcher:
    """Создаёт Dispatcher и подключает роутеры приложения.

    Args:
        storage: Хранилище FSM-состояний.

    Returns:
        Настроенный Dispatcher.
    """
    dispatcher = Dispatcher(storage=storage)
    dispatcher.include_router(router)
    dispatcher.errors.register(on_error)
    return dispatcher


def create_webhook_app(*, bot: Bot, dispatcher: Dispatcher) -> web.Application:
    """Создаёт aiohttp-приложение для обработки Telegram webhook-запросов.

    Args:
        bot: Экземпляр Telegram-бота.
        dispatcher: Dispatcher приложения.

    Returns:
        Настроенное aiohttp-приложение.

    Raises:
        ValueError: Если не задан базовый URL webhook.
        ValueError: Если не задан секрет webhook.
    """
    if not settings.bot.WEBHOOK_BASE_URL:
        raise ValueError("WEBHOOK_BASE_URL is required in webhook mode")
    if not settings.bot.WEBHOOK_SECRET:
        raise ValueError("WEBHOOK_SECRET is required in webhook mode")

    application = web.Application()
    SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=settings.bot.WEBHOOK_SECRET,
    ).register(application, path="/webhook")
    setup_application(application, dispatcher, bot=bot)
    return application


async def run_webhook(*, bot: Bot, dispatcher: Dispatcher, shutdown_event: asyncio.Event) -> None:
    """Запускает webhook-сервер на всех интерфейсах контейнера.

    Args:
        bot: Экземпляр Telegram-бота.
        dispatcher: Dispatcher приложения.
        shutdown_event: Событие для координации остановки сервера.
    """
    webhook_url = f"{settings.bot.WEBHOOK_BASE_URL.rstrip('/')}/webhook"
    await bot.set_webhook(
        url=webhook_url,
        secret_token=settings.bot.WEBHOOK_SECRET,
        drop_pending_updates=True,
    )

    application = create_webhook_app(bot=bot, dispatcher=dispatcher)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=8000)
    await site.start()
    log.info("Webhook server started on port 8000")

    try:
        await shutdown_event.wait()
    finally:
        log.info("Stopping webhook server")
        await runner.cleanup()


async def main() -> None:
    """Точка входа: настраивает логирование, Bot и Dispatcher, запускает выбранный режим.

    Обрабатывает SIGTERM и SIGINT для graceful shutdown при деплое и остановке контейнера.
    Все ресурсы закрываются в finally, чтобы при любой ошибке не оставлять висящих соединений.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    log.info("Starting bot (mock_mode=%s)", settings.generation.MOCK_MODE)

    if not settings.bot.BOT_TOKEN:
        log.error("BOT_TOKEN is not set — startup is impossible")
        return

    # Событие для координации graceful shutdown между обработчиками сигналов и основным циклом
    shutdown_event = asyncio.Event()

    def signal_handler(signum: int) -> None:
        """Обработчик SIGTERM/SIGINT: устанавливает флаг остановки."""
        sig_name = signal.Signals(signum).name
        log.info("Received signal %s, initiating graceful shutdown", sig_name)
        shutdown_event.set()

    # Регистрируем обработчики сигналов
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler, sig)

    bot: Bot | None = None
    storage: RedisStorage | None = None

    try:
        # Задачи генерации рестарт не переживают, а FSM в Redis — да:
        # чистим осиротевшие флаги generating, иначе пользователь
        # останется с «Дождитесь окончания текущей генерации» навсегда
        await clear_orphaned_generation_flags()
        # Реестр активных задач хранит uid в Redis: после рестарта записи
        # неактуальны, сами задачи в памяти процесса не выжили
        await clear_active_tasks()

        bot = Bot(
            token=settings.bot.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        # FSM-состояния храним в Redis: данные переживают рестарт контейнера
        storage = RedisStorage.from_url(settings.redis.redis_url)
        dp = create_dispatcher(storage=storage)

        if settings.bot.WEBHOOK_MODE:
            await run_webhook(bot=bot, dispatcher=dp, shutdown_event=shutdown_event)
        else:
            # start_polling сам обрабатывает graceful shutdown при получении сигнала
            await dp.start_polling(bot, handle_signals=False)

    except Exception as e:
        log.exception("Fatal error during bot execution: %s", e)
    finally:
        if storage:
            await storage.close()
        await shutdown_all(bot=bot)


if __name__ == "__main__":
    asyncio.run(main())
