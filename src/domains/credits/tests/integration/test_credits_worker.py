"""Интеграционный тест воркера credits через InMemoryBroker."""

import pytest
from taskiq import InMemoryBroker

from shared.contracts.commands import GetCredits
from shared.ports.fake_telegram import FakeTelegramPort


@pytest.fixture
def fake_telegram_port() -> FakeTelegramPort:
    """Фейковый порт Telegram для тестов воркера.

    Returns:
        Экземпляр фейкового порта.
    """
    return FakeTelegramPort()


@pytest.fixture
async def inmemory_broker_with_port(
    inmemory_broker: InMemoryBroker,
    fake_telegram_port: FakeTelegramPort,
) -> InMemoryBroker:
    """InMemoryBroker с зарегистрированным telegram_port и задачей handle_get_credits.

    Args:
        inmemory_broker: Фикстура InMemoryBroker из conftest.
        fake_telegram_port: Фейковый порт Telegram.

    Returns:
        Брокер с портом в состоянии и зарегистрированной задачей.
    """
    from domains.credits.worker import handle_get_credits

    # Регистрируем задачу через декоратор broker.task
    inmemory_broker.register_task(
        handle_get_credits.original_func,
        task_name="credits.get_credits",
        labels={"queue_name": "dev.credits.tasks"},
    )

    # Добавляем порт в состояние
    await inmemory_broker.startup()
    inmemory_broker.state["telegram_port"] = fake_telegram_port

    yield inmemory_broker

    await inmemory_broker.shutdown()


async def test_handle_get_credits_success(
    inmemory_broker_with_port: InMemoryBroker,
    fake_telegram_port: FakeTelegramPort,
    monkeypatch,
) -> None:
    """Воркер успешно обрабатывает GetCredits и отправляет сообщение пользователю.

    Args:
        inmemory_broker_with_port: InMemoryBroker с портом.
        fake_telegram_port: Фейковый порт Telegram.
        monkeypatch: Pytest monkeypatch для подмены get_credits_summary.
    """
    from domains.credits.service import CreditsSummary
    from domains.credits.worker import handle_get_credits

    async def mock_get_credits_summary(*, api_key: str, song_price: float) -> CreditsSummary:
        return CreditsSummary(
            status_code=200,
            total_songs=100,
            used_songs=30,
            remaining_songs=70,
        )

    monkeypatch.setattr("domains.credits.worker.get_credits_summary", mock_get_credits_summary)

    command = GetCredits(chat_id=12345)

    # Создаём минимальный Context с нужными полями
    class FakeContext:
        def __init__(self, state):
            self.state = state

    context = FakeContext(state=inmemory_broker_with_port.state)
    await handle_get_credits.original_func(command=command, context=context)

    assert len(fake_telegram_port.sent_messages) == 1
    sent = fake_telegram_port.sent_messages[0]
    assert sent["chat_id"] == 12345
    assert "100" in sent["text"]
    assert "30" in sent["text"]
    assert "70" in sent["text"]


async def test_handle_get_credits_api_error(
    inmemory_broker_with_port: InMemoryBroker,
    fake_telegram_port: FakeTelegramPort,
    monkeypatch,
) -> None:
    """Воркер обрабатывает ошибку API OpenRouter.

    Args:
        inmemory_broker_with_port: InMemoryBroker с портом.
        fake_telegram_port: Фейковый порт Telegram.
        monkeypatch: Pytest monkeypatch для подмены get_credits_summary.
    """
    from domains.credits.service import CreditsSummary
    from domains.credits.worker import handle_get_credits

    async def mock_get_credits_summary(*, api_key: str, song_price: float) -> CreditsSummary:
        return CreditsSummary(status_code=401)

    monkeypatch.setattr("domains.credits.worker.get_credits_summary", mock_get_credits_summary)

    command = GetCredits(chat_id=12345)

    # Создаём минимальный Context с нужными полями
    class FakeContext:
        def __init__(self, state):
            self.state = state

    context = FakeContext(state=inmemory_broker_with_port.state)
    await handle_get_credits.original_func(command=command, context=context)

    assert len(fake_telegram_port.sent_messages) == 1
    sent = fake_telegram_port.sent_messages[0]
    assert sent["chat_id"] == 12345
    assert "401" in sent["text"]


async def test_handle_get_credits_network_error(
    inmemory_broker_with_port: InMemoryBroker,
    fake_telegram_port: FakeTelegramPort,
    monkeypatch,
) -> None:
    """Воркер обрабатывает сетевую ошибку при запросе к OpenRouter.

    Args:
        inmemory_broker_with_port: InMemoryBroker с портом.
        fake_telegram_port: Фейковый порт Telegram.
        monkeypatch: Pytest monkeypatch для подмены get_credits_summary.
    """
    import httpx

    from domains.credits.worker import handle_get_credits

    async def mock_get_credits_summary(*, api_key: str, song_price: float):
        raise httpx.HTTPError("Network error")

    monkeypatch.setattr("domains.credits.worker.get_credits_summary", mock_get_credits_summary)

    command = GetCredits(chat_id=12345)

    # Создаём минимальный Context с нужными полями
    class FakeContext:
        def __init__(self, state):
            self.state = state

    context = FakeContext(state=inmemory_broker_with_port.state)
    await handle_get_credits.original_func(command=command, context=context)

    # Проверяем, что отправлено сообщение об ошибке
    assert len(fake_telegram_port.sent_messages) == 1
    assert fake_telegram_port.sent_messages[0]["chat_id"] == 12345

    # Проверяем, что владелец уведомлён
    assert len(fake_telegram_port.owner_notifications) == 1
