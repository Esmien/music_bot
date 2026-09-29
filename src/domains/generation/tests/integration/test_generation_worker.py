"""Интеграционные тесты worker-а генерации через контракты и TelegramPort."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from domains.evaluation.evaluation_messages import EVALUATION_PROMPT_TEXT
from domains.feedback.fsm import FeedbackStates
from domains.generation import worker
from domains.generation.models import Generation, GenerationStatus
from domains.generation.state_models import GenerationFlowState
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

    async def execute(self, statement: Any) -> Any:
        if self.generation and self.generation.status == GenerationStatus.PENDING:
            self.generation.status = GenerationStatus.PROCESSING
            return SimpleNamespace(scalar_one_or_none=lambda: self.generation.id)
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def get(self, model: type[Generation], gen_id: int) -> Generation | None:
        return self.generation

    async def commit(self) -> None:
        self.committed = True


def _context(telegram: FakeTelegramPort, bot=None, storage=None) -> SimpleNamespace:
    """Создаёт TaskIQ-контекст с TelegramPort, Bot и Storage.

    Args:
        telegram: Фейковый Telegram-порт.
        bot: Фейковый Bot.
        storage: Фейковое FSM-хранилище.

    Returns:
        Контекст с зарегистрированными зависимостями.
    """
    dependencies = {"telegram_port": telegram}
    if bot is not None:
        dependencies["bot"] = bot
    if storage is not None:
        dependencies["storage"] = storage
    return SimpleNamespace(dependencies=dependencies)


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


async def test_worker_publishes_success_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешная генерация отправляет аудио и запрашивает оценку."""
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
    bot = SimpleNamespace(id=123)
    storage = MemoryStorage()

    key = StorageKey(bot_id=bot.id, chat_id=20, user_id=10)
    fsm_context = FSMContext(storage=storage, key=key)
    await fsm_context.set_data(GenerationFlowState(gen_id=1, generating=True).model_dump())

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        await on_progress(stage="Получаю аудио…", fraction=0.5)
        return b"audio"

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "clear_generation_cancel", AsyncMock())

    await worker.run_generation_task(_command(), _context(telegram, bot=bot, storage=storage))

    assert len(telegram.sent_audio) == 1
    assert telegram.sent_audio[0]["chat_id"] == 20
    assert telegram.sent_audio[0]["audio_type"] == "bytes"
    assert telegram.sent_audio[0]["title"] == "Тест"
    assert telegram.edited_messages
    assert generation.status is GenerationStatus.SUCCESS
    assert session.committed
    assert len(telegram.sent_messages) == 1
    assert telegram.sent_messages[0]["text"] == EVALUATION_PROMPT_TEXT
    assert await fsm_context.get_state() == FeedbackStates.waiting_evaluation.state


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


async def test_worker_atomic_claim_concurrent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Конкурентный запуск двух воркеров приводит ровно к одному вызову run_generation."""
    generation = Generation(
        id=4,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.PENDING,
    )
    lock = asyncio.Lock()
    call_count = 0

    class ConcurrentSession:
        async def __aenter__(self) -> "ConcurrentSession":
            return self

        async def __aexit__(self, exc_type, exc_value, traceback) -> None:
            return None

        async def execute(self, statement: Any) -> Any:
            async with lock:
                if generation.status == GenerationStatus.PENDING:
                    generation.status = GenerationStatus.PROCESSING
                    return SimpleNamespace(scalar_one_or_none=lambda: generation.id)
                return SimpleNamespace(scalar_one_or_none=lambda: None)

        async def get(self, model: type[Generation], gen_id: int) -> Generation | None:
            return generation

        async def commit(self) -> None:
            pass

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return b"audio"

    telegram = FakeTelegramPort()
    monkeypatch.setattr(worker, "get_session", ConcurrentSession)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))

    command = _command(gen_id=4)
    context = _context(telegram)

    await asyncio.gather(
        worker.run_generation_task(command=command, context=context),
        worker.run_generation_task(command=command, context=context),
    )

    assert call_count == 1
    assert generation.status is GenerationStatus.SUCCESS
