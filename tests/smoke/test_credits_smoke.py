"""Smoke-тест credits через InMemoryBroker."""

from shared.contracts.commands import GetCredits
from shared.ports.fake_telegram import FakeTelegramPort


async def test_credits_inmemory_smoke(inmemory_broker) -> None:
    """Проверяет публикацию GetCredits через InMemoryBroker.

    Args:
        inmemory_broker: In-memory брокер из фикстуры.
    """

    @inmemory_broker.task(task_name="test_get_credits")
    async def test_handle_get_credits(command: GetCredits) -> str:
        """Тестовая задача для публикации."""
        return f"Processed credits for chat_id={command.chat_id}"

    fake_port = FakeTelegramPort()
    inmemory_broker.state["telegram_port"] = fake_port

    command = GetCredits(chat_id=99999)
    task = await test_handle_get_credits.kiq(command)

    # InMemoryBroker выполняет задачи синхронно
    result = await task.wait_result()

    assert result.is_err is False
    assert "Processed credits for chat_id=99999" in str(result.return_value)
