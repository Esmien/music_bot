"""Интеграционные тесты worker-а генерации через контракты и TelegramPort."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from domains.generation import worker
from domains.generation.models import Generation, GenerationStatus
from shared.contracts.commands import RunGeneration
from shared.ports.fake_telegram import FakeTelegramPort

pytestmark = pytest.mark.integration


class FakeSession:
    """Минимальная async-сессия для проверки worker-сценариев."""

    def __init__(self, generation: Generation | None) -> None:
        self.generation = generation
        self.committed = False

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None

    async def get(self, model: type[Generation], gen_id: int) -> Generation | None:
        return self.generation

    async def commit(self) -> None:
        self.committed = True


class FakeBroker:
    """Перехватывает события, опубликованные worker-ом."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def create_fake_task(self, task_name: str):
        """Создаёт фейковый таск для подмены."""
        events = self.events

        class FakeTask:
            async def kiq(self, event: object) -> None:
                events.append((task_name, event))

        return FakeTask()


@pytest.fixture
def fake_broker():
    """Фейковый брокер для перехвата событий."""
    return FakeBroker()


def _context(telegram: FakeTelegramPort) -> SimpleNamespace:
    """Создаёт TaskIQ-контекст с TelegramPort.

    Args:
        telegram: Фейковый Telegram-порт.

    Returns:
        Контекст с зарегистрированной зависимостью.
    """
    return SimpleNamespace(dependencies={"telegram_port": telegram})


def _command(gen_id: int = 1) -> RunGeneration:
    """Создаёт команду генерации для теста.

    Args:
        gen_id: Идентификатор генерации.

    Returns:
        Команда RunGeneration.
    """
    return RunGeneration(
        user_id=10,
        chat_id=20,
        gen_id=gen_id,
        prompt="тестовый промпт",
        title="Тест",
        status_message_id=30,
    )


async def test_worker_publishes_success_event(monkeypatch: pytest.MonkeyPatch, fake_broker: FakeBroker) -> None:
    """Успешная генерация отправляет аудио и публикует событие."""
    generation = Generation(
        id=1,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.PENDING,
    )
    session = FakeSession(generation)
    telegram = FakeTelegramPort()

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        await on_progress(stage="Получаю аудио…", fraction=0.5)
        return b"audio"

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "clear_generation_cancel", AsyncMock())
    monkeypatch.setattr(
        worker, "request_evaluation_handler", fake_broker.create_fake_task("request_evaluation_handler")
    )

    await worker.run_generation_task(_command(), _context(telegram))

    assert len(telegram.sent_audio) == 1
    assert telegram.sent_audio[0]["chat_id"] == 20
    assert telegram.sent_audio[0]["audio_type"] == "bytes"
    assert telegram.sent_audio[0]["title"] == "Тест"
    assert telegram.edited_messages
    assert fake_broker.events[0][0] == "request_evaluation_handler"
    assert fake_broker.events[0][1].gen_id == 1


async def test_worker_marks_cancelled_generation_and_publishes_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel-token прерывает генерацию и публикует событие отмены."""
    generation = Generation(
        id=2,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.PENDING,
    )
    session = FakeSession(generation)
    telegram = FakeTelegramPort()

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        await on_progress(stage="Получаю аудио…", fraction=0.5)
        return b"audio"

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "clear_generation_cancel", AsyncMock())

    await worker.run_generation_task(_command(gen_id=2), _context(telegram))

    assert generation.status is GenerationStatus.CANCELLED
    assert session.committed
    assert len(telegram.sent_messages) == 1
    assert "отменена" in telegram.sent_messages[0]["text"]


async def test_worker_skips_already_processed_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повторная команда с тем же gen_id не запускает генерацию повторно."""
    generation = Generation(
        id=3,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.SUCCESS,
    )
    session = FakeSession(generation)
    telegram = FakeTelegramPort()
    run_generation = AsyncMock()

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", run_generation)

    await worker.run_generation_task(_command(gen_id=3), _context(telegram))

    run_generation.assert_not_awaited()
    assert not telegram.sent_audio
