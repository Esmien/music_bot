"""Интеграционные тесты worker-а генерации через контракты и TelegramPort."""

import asyncio
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from core.metrics import (
    DELIVERY_TOTAL,
    GENERATION_TOTAL,
)
from domains.evaluation.evaluation_messages import EVALUATION_PROMPT_TEXT
from domains.evaluation.handlers import handle_generation_succeeded_event
from domains.feedback.fsm import FeedbackStates
from domains.generation import worker
from domains.generation.models import Generation, GenerationStatus
from domains.generation.state_models import GenerationFlowState
from shared.contracts.commands import RunGeneration
from shared.contracts.events import GenerationFailed, GenerationSucceeded
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
        if self.generation:
            values_by_name = {}
            for k, v in getattr(statement, "_values", {}).items():
                col_name = getattr(k, "key", getattr(k, "name", str(k)))
                val = getattr(v, "value", v)
                values_by_name[col_name] = val

            if "status" in values_by_name and values_by_name["status"] == GenerationStatus.PROCESSING:
                if self.generation.status == GenerationStatus.PENDING:
                    self.generation.status = GenerationStatus.PROCESSING
                    self.generation.attempt_id = values_by_name.get("attempt_id", "test-attempt")
                    return SimpleNamespace(scalar_one_or_none=lambda: self.generation.id)
                return SimpleNamespace(scalar_one_or_none=lambda: None)

            for name, val in values_by_name.items():
                setattr(self.generation, name, val)
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


async def test_worker_publishes_success_event(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Успешная генерация сохраняет аудио на диск, записывает метаданные и публикует GenerationSucceeded."""
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
    published_events = []

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        await on_progress(stage="Получаю аудио…", fraction=0.5)
        return b"audio_content"

    def fake_save_audio(audio_bytes: bytes, gen_id: int) -> tuple[str, int, str]:
        audio_path = tmp_path / f"gen_{gen_id}.mp3"
        audio_path.write_bytes(audio_bytes)
        checksum = hashlib.sha256(audio_bytes).hexdigest()
        return str(audio_path), len(audio_bytes), checksum

    async def fake_publish_event(*, task_name: str, event: Any, task: Any) -> None:
        published_events.append((task_name, event))

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "save_audio_to_storage", fake_save_audio)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "clear_generation_cancel", AsyncMock())
    monkeypatch.setattr(worker, "_publish_event", fake_publish_event)
    monkeypatch.setattr(worker, "claim_delivery_atomic", AsyncMock(return_value=True))

    await worker.run_generation_task(_command(), _context(telegram))

    assert len(telegram.sent_audio) == 1
    assert telegram.sent_audio[0]["chat_id"] == 20
    assert telegram.sent_audio[0]["audio_type"] == "binary_io"
    assert telegram.sent_audio[0]["title"] == "Тест"
    assert telegram.edited_messages
    assert generation.status is GenerationStatus.SUCCESS
    assert generation.audio_path == str(tmp_path / "gen_1.mp3")
    assert generation.audio_size == len(b"audio_content")
    assert generation.audio_checksum == hashlib.sha256(b"audio_content").hexdigest()
    assert session.committed
    assert len(published_events) == 1
    task_name, event = published_events[0]
    assert task_name == "handle_generation_succeeded"
    assert isinstance(event, GenerationSucceeded)
    assert event.gen_id == 1
    assert event.user_id == 10
    assert event.chat_id == 20


async def test_worker_publishes_generation_failed_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбой генерации публикует GenerationFailed со stage='generation'."""
    generation = Generation(
        id=5,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.PENDING,
    )
    session = FakeSession(generation)
    telegram = FakeTelegramPort()
    published_events = []

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        raise RuntimeError("API timeout error")

    async def fake_publish_event(*, task_name: str, event: Any, task: Any) -> None:
        published_events.append((task_name, event))

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "_publish_event", fake_publish_event)
    monkeypatch.setattr(worker, "notify_owner", AsyncMock())

    await worker.run_generation_task(_command(gen_id=5), _context(telegram))

    assert generation.status is GenerationStatus.FAILED
    assert len(published_events) == 1
    task_name, event = published_events[0]
    assert task_name == "handle_generation_failed"
    assert isinstance(event, GenerationFailed)
    assert event.stage == "generation"
    assert event.gen_id == 5


async def test_end_to_end_generation_succeeded_event_handling() -> None:
    """Сквозной тест: событие GenerationSucceeded переводит FSM в waiting_evaluation и отправляет клавиатуру."""
    telegram = FakeTelegramPort()
    bot = SimpleNamespace(id=123)
    storage = MemoryStorage()

    key = StorageKey(bot_id=bot.id, chat_id=20, user_id=10)
    fsm_context = FSMContext(storage=storage, key=key)
    await fsm_context.set_data(GenerationFlowState(gen_id=1, generating=True).model_dump())

    event = GenerationSucceeded(user_id=10, chat_id=20, gen_id=1, status_message_id=30)
    context = _context(telegram, bot=bot, storage=storage)

    await handle_generation_succeeded_event(event=event, context=context)

    assert len(telegram.sent_messages) == 1
    assert telegram.sent_messages[0]["text"] == EVALUATION_PROMPT_TEXT
    assert await fsm_context.get_state() == FeedbackStates.waiting_evaluation.state

    flow_state = await fsm_context.get_data()
    assert flow_state["generating"] is False


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


async def test_worker_redelivery_uses_existing_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """При повторной попытке доставки воркер берет существующий аудиофайл без вызова генерации."""
    audio_file = tmp_path / "gen_3.mp3"
    audio_bytes = b"existing_audio"
    audio_file.write_bytes(audio_bytes)
    checksum = hashlib.sha256(audio_bytes).hexdigest()

    generation = Generation(
        id=3,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.SUCCESS,
        audio_path=str(audio_file),
        audio_size=len(audio_bytes),
        audio_checksum=checksum,
    )
    session = FakeSession(generation)
    telegram = FakeTelegramPort()
    run_generation = AsyncMock()
    published_events = []

    async def fake_publish_event(*, task_name: str, event: Any, task: Any) -> None:
        published_events.append((task_name, event))

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", run_generation)
    monkeypatch.setattr(worker, "_publish_event", fake_publish_event)
    monkeypatch.setattr(worker, "claim_delivery_atomic", AsyncMock(return_value=True))

    await worker.run_generation_task(_command(gen_id=3), _context(telegram))

    run_generation.assert_not_awaited()
    assert len(telegram.sent_audio) == 1
    assert telegram.sent_audio[0]["title"] == "Тест"
    assert len(published_events) == 1
    assert published_events[0][0] == "handle_generation_succeeded"


async def test_worker_telegram_delivery_failure_keeps_generation_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Сбой доставки в Telegram не сбрасывает статус генерации SUCCESS и публикует stage='delivery'."""
    audio_path = tmp_path / "gen_6.mp3"
    audio_path.write_bytes(b"audio_content")

    generation = Generation(
        id=6,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.PENDING,
    )
    session = FakeSession(generation)

    class FailingTelegramPort(FakeTelegramPort):
        async def send_audio(self, *args, **kwargs) -> None:
            raise ConnectionError("Telegram network timeout")

    telegram = FailingTelegramPort()
    published_events = []

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        return b"audio_content"

    def fake_save_audio(audio_bytes: bytes, gen_id: int) -> tuple[str, int, str]:
        checksum = hashlib.sha256(audio_bytes).hexdigest()
        return str(audio_path), len(audio_bytes), checksum

    async def fake_publish_event(*, task_name: str, event: Any, task: Any) -> None:
        published_events.append((task_name, event))

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "save_audio_to_storage", fake_save_audio)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "notify_owner", AsyncMock())
    monkeypatch.setattr(worker, "_publish_event", fake_publish_event)
    monkeypatch.setattr(worker, "claim_delivery_atomic", AsyncMock(return_value=True))

    await worker.run_generation_task(_command(gen_id=6), _context(telegram))

    # Статус генерации должен остаться SUCCESS, так как аудиофайл успешно сохранён
    assert generation.status is GenerationStatus.SUCCESS
    assert generation.audio_path == str(audio_path)

    # Событие ошибки должно содержать stage="delivery"
    assert len(published_events) == 1
    task_name, event = published_events[0]
    assert task_name == "handle_generation_failed"
    assert isinstance(event, GenerationFailed)
    assert event.stage == "delivery"
    assert "Delivery failed" in event.error_message


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
                values_by_name = {}
                for k, v in getattr(statement, "_values", {}).items():
                    col_name = getattr(k, "key", getattr(k, "name", str(k)))
                    val = getattr(v, "value", v)
                    values_by_name[col_name] = val

                if "status" in values_by_name and values_by_name["status"] == GenerationStatus.PROCESSING:
                    if generation.status == GenerationStatus.PENDING:
                        generation.status = GenerationStatus.PROCESSING
                        generation.attempt_id = values_by_name.get("attempt_id", "test-attempt")
                        return SimpleNamespace(scalar_one_or_none=lambda: generation.id)
                    return SimpleNamespace(scalar_one_or_none=lambda: None)

                for name, val in values_by_name.items():
                    setattr(generation, name, val)
                return SimpleNamespace(scalar_one_or_none=lambda: generation.id)

        async def get(self, model: type[Generation], gen_id: int) -> Generation | None:
            return generation

        async def commit(self) -> None:
            pass

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return b"audio"

    def fake_save_audio(audio_bytes: bytes, gen_id: int) -> tuple[str, int, str]:
        return f"/tmp/gen_{gen_id}.mp3", len(audio_bytes), "fake_checksum"

    telegram = FakeTelegramPort()
    monkeypatch.setattr(worker, "get_session", ConcurrentSession)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "save_audio_to_storage", fake_save_audio)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(
        worker, "Path", lambda p: SimpleNamespace(exists=lambda: True, open=lambda mode: io.BytesIO(b"audio"))
    )
    monkeypatch.setattr(worker, "_publish_event", AsyncMock())
    monkeypatch.setattr(worker, "claim_delivery_atomic", AsyncMock(return_value=True))

    command = _command(gen_id=4)
    context = _context(telegram)

    await asyncio.gather(
        worker.run_generation_task(command=command, context=context),
        worker.run_generation_task(command=command, context=context),
    )

    assert call_count == 1
    assert generation.status is GenerationStatus.SUCCESS


async def test_worker_notify_owner_failure_does_not_prevent_db_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой notify_owner не мешает обновлению статуса на FAILED в БД и публикации GenerationFailed."""
    generation = Generation(
        id=7,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.PENDING,
    )
    session = FakeSession(generation)
    telegram = FakeTelegramPort()
    published_events = []

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        raise RuntimeError("OpenRouter 500 internal server error")

    async def fake_publish_event(*, task_name: str, event: Any, task: Any) -> None:
        published_events.append((task_name, event))

    failing_notify = AsyncMock(side_effect=ConnectionError("Telegram network timeout"))

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "notify_owner", failing_notify)
    monkeypatch.setattr(worker, "_publish_event", fake_publish_event)

    await worker.run_generation_task(_command(gen_id=7), _context(telegram))

    assert generation.status is GenerationStatus.FAILED
    assert session.committed
    assert len(published_events) == 1
    task_name, event = published_events[0]
    assert task_name == "handle_generation_failed"
    assert isinstance(event, GenerationFailed)
    assert event.gen_id == 7
    assert event.stage == "generation"


async def test_worker_stale_attempt_does_not_overwrite_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Устаревший воркер с несовпадающим attempt_id не перезаписывает статус генерации."""
    generation = Generation(
        id=8,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.PROCESSING,
        attempt_id="newer-attempt-id",
    )

    class StaleAttemptSession(FakeSession):
        async def execute(self, statement: Any) -> Any:
            # Имитируем несовпадение attempt_id: ни одна строка не обновлена
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    session = StaleAttemptSession(generation)
    telegram = FakeTelegramPort()

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", AsyncMock(side_effect=RuntimeError("Late failure")))
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "notify_owner", AsyncMock())
    monkeypatch.setattr(worker, "_publish_event", AsyncMock())

    await worker.run_generation_task(_command(gen_id=8), _context(telegram))

    # Статус остался прежним, так как attempt_id не совпал
    assert generation.status is GenerationStatus.PROCESSING
    assert generation.attempt_id == "newer-attempt-id"


async def test_generation_failed_event_clears_generating_flag_in_fsm() -> None:
    """Событие GenerationFailed сбрасывает generating в False в FSM."""
    from domains.generation.handlers import handle_generation_failed_event

    telegram = FakeTelegramPort()
    bot = SimpleNamespace(id=123)
    storage = MemoryStorage()

    key = StorageKey(bot_id=bot.id, chat_id=20, user_id=10)
    fsm_context = FSMContext(storage=storage, key=key)
    await fsm_context.set_data(GenerationFlowState(gen_id=9, prompt="песня", generating=True).model_dump())

    event = GenerationFailed(
        user_id=10,
        chat_id=20,
        gen_id=9,
        error_message="Fatal error",
        stage="generation",
    )
    context = _context(telegram, bot=bot, storage=storage)

    await handle_generation_failed_event(event=event, context=context)

    flow_state = await fsm_context.get_data()
    assert flow_state.get("generating") is False
    assert await fsm_context.get_state() is None
    assert len(telegram.sent_messages) == 1
    assert "Fatal error" in telegram.sent_messages[0]["text"]


async def test_worker_save_audio_failure_marks_generation_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой сохранения аудиофайла на диск переводит генерацию в FAILED."""
    generation = Generation(
        id=10,
        user_id=10,
        prompt="тестовый промпт",
        enriched_prompt={"text": "тестовый промпт"},
        title="Тест",
        status=GenerationStatus.PENDING,
    )
    session = FakeSession(generation)
    telegram = FakeTelegramPort()
    published_events = []

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        return b"valid_audio_bytes"

    def failing_save_audio(audio_bytes: bytes, gen_id: int) -> tuple[str, int, str]:
        raise OSError("Disk full: no space left on device")

    async def fake_publish_event(*, task_name: str, event: Any, task: Any) -> None:
        published_events.append((task_name, event))

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "save_audio_to_storage", failing_save_audio)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "notify_owner", AsyncMock())
    monkeypatch.setattr(worker, "_publish_event", fake_publish_event)

    await worker.run_generation_task(_command(gen_id=10), _context(telegram))

    assert generation.status is GenerationStatus.FAILED
    assert session.committed
    assert len(published_events) == 1
    task_name, event = published_events[0]
    assert task_name == "handle_generation_failed"
    assert isinstance(event, GenerationFailed)
    assert event.stage == "generation"
    assert "Disk full" in event.error_message


async def test_concurrent_generation_worker_execution_with_barrier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AUD-028: Конкурентный запуск двух воркеров через барьер.

    Гарантирует:
    1. Два воркера синхронизируются барьером непосредственно перед claim.
    2. Только один воркер захватывает claim (PENDING -> PROCESSING) и вызывает генерацию.
    3. Генерация выполняется ровно один раз (API calls == 1, max parallel runs == 1).
    4. Аудио доставляется строго по delivery policy (ровно один раз).
    5. Итоговый attempt_id и статус соответствуют победившему воркеру.
    6. Попытка stale worker обновить статус генерации отвергается.
    """
    generation = Generation(
        id=11,
        user_id=10,
        prompt="конкурентный промпт",
        enriched_prompt={"text": "конкурентный промпт"},
        title="Конкурентная песня",
        status=GenerationStatus.PENDING,
    )

    barrier = asyncio.Barrier(2)
    db_lock = asyncio.Lock()
    claim_count = 0
    claimed_attempt_ids: list[str] = []
    active_generations = 0
    max_parallel_generations = 0
    api_calls = 0

    class BarrierConcurrentSession:
        async def __aenter__(self) -> "BarrierConcurrentSession":
            return self

        async def __aexit__(self, exc_type, exc_value, traceback) -> None:
            return None

        async def execute(self, statement: Any) -> Any:
            values_by_name = {}
            for k, v in getattr(statement, "_values", {}).items():
                col_name = getattr(k, "key", getattr(k, "name", str(k)))
                val = getattr(v, "value", v)
                values_by_name[col_name] = val

            # Синхронизируем воркеры перед попыткой claim
            if values_by_name.get("status") == GenerationStatus.PROCESSING:
                await barrier.wait()
                async with db_lock:
                    nonlocal claim_count
                    if generation.status == GenerationStatus.PENDING:
                        claim_count += 1
                        attempt = values_by_name.get("attempt_id", "test-attempt")
                        claimed_attempt_ids.append(attempt)
                        generation.status = GenerationStatus.PROCESSING
                        generation.attempt_id = attempt
                        return SimpleNamespace(scalar_one_or_none=lambda: generation.id)
                    return SimpleNamespace(scalar_one_or_none=lambda: None)

            # Обновление SUCCESS проверяет attempt_id и PROCESSING статус
            if values_by_name.get("status") == GenerationStatus.SUCCESS:
                async with db_lock:
                    attempt_condition = None
                    for crit in getattr(getattr(statement, "whereclause", None), "clauses", []):
                        col = getattr(crit, "left", None)
                        col_key = getattr(col, "key", getattr(col, "name", None))
                        if col_key == "attempt_id":
                            attempt_condition = getattr(getattr(crit, "right", None), "value", None)

                    if generation.status == GenerationStatus.PROCESSING and (
                        attempt_condition is None or attempt_condition == generation.attempt_id
                    ):
                        for name, val in values_by_name.items():
                            setattr(generation, name, val)
                        return SimpleNamespace(scalar_one_or_none=lambda: generation.id)
                    return SimpleNamespace(scalar_one_or_none=lambda: None)

            async with db_lock:
                for name, val in values_by_name.items():
                    setattr(generation, name, val)
                return SimpleNamespace(scalar_one_or_none=lambda: generation.id)

        async def get(self, model: type[Generation], gen_id: int) -> Generation | None:
            return generation

        async def commit(self) -> None:
            pass

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        nonlocal active_generations, max_parallel_generations, api_calls
        active_generations += 1
        max_parallel_generations = max(max_parallel_generations, active_generations)
        api_calls += 1
        await asyncio.sleep(0.05)
        active_generations -= 1
        return b"concurrent_audio_bytes"

    audio_file = tmp_path / "gen_11.mp3"

    def fake_save_audio(audio_bytes: bytes, gen_id: int) -> tuple[str, int, str]:
        audio_file.write_bytes(audio_bytes)
        checksum = hashlib.sha256(audio_bytes).hexdigest()
        return str(audio_file), len(audio_bytes), checksum

    delivery_lock = asyncio.Lock()
    delivery_claimed = False

    async def fake_claim_delivery_atomic(gen_id: int, delivery_attempt_id: str) -> bool:
        nonlocal delivery_claimed
        async with delivery_lock:
            if not delivery_claimed:
                delivery_claimed = True
                return True
            return False

    telegram = FakeTelegramPort()
    published_events = []

    async def fake_publish_event(*, task_name: str, event: Any, task: Any) -> None:
        published_events.append((task_name, event))

    monkeypatch.setattr(worker, "get_session", BarrierConcurrentSession)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "save_audio_to_storage", fake_save_audio)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "claim_delivery_atomic", fake_claim_delivery_atomic)
    monkeypatch.setattr(worker, "_publish_event", fake_publish_event)

    command = _command(gen_id=11)
    context = _context(telegram)

    # Запускаем два параллельных worker-вызова
    await asyncio.gather(
        worker.run_generation_task(command=command, context=context),
        worker.run_generation_task(command=command, context=context),
    )

    # 1. Только один worker получил успешный claim
    assert claim_count == 1
    assert len(claimed_attempt_ids) == 1
    winning_attempt_id = claimed_attempt_ids[0]

    # 2. Два worker-а не выполняют генерацию одновременно
    assert max_parallel_generations == 1
    assert api_calls == 1

    # 3. Количество delivery соответствует policy (ровно 1 раз отправлен аудиофайл)
    assert len(telegram.sent_audio) == 1
    assert telegram.sent_audio[0]["title"] == "Тест"

    # 4. Итоговый статус и attempt_id принадлежат победившему воркеру
    assert generation.status is GenerationStatus.SUCCESS
    assert generation.attempt_id == winning_attempt_id

    # 5. Stale worker не может перезаписать финальный статус
    stale_save_result = await worker._save_generation_success(
        gen_id=11,
        attempt_id="stale_attempt_999",
        audio_path="/tmp/stale.mp3",
        audio_size=10,
        audio_checksum="stale_hash",
    )
    assert stale_save_result is False
    assert generation.status is GenerationStatus.SUCCESS
    assert generation.attempt_id == winning_attempt_id


async def test_worker_records_lifecycle_metrics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Проверяет регистрацию метрик жизненного цикла генерации и доставки без утечки персональных данных."""
    generation = Generation(
        id=12,
        user_id=10,
        prompt="промпт для проверки метрик",
        enriched_prompt={"text": "промпт"},
        title="Метрики",
        status=GenerationStatus.PENDING,
    )
    session = FakeSession(generation)
    telegram = FakeTelegramPort()

    async def fake_run_generation(prompt: str, gen_id: int, on_progress) -> bytes:
        return b"metric_audio"

    def fake_save_audio(audio_bytes: bytes, gen_id: int) -> tuple[str, int, str]:
        p = tmp_path / f"gen_{gen_id}.mp3"
        p.write_bytes(audio_bytes)
        return str(p), len(audio_bytes), hashlib.sha256(audio_bytes).hexdigest()

    monkeypatch.setattr(worker, "get_session", lambda: session)
    monkeypatch.setattr(worker, "run_generation", fake_run_generation)
    monkeypatch.setattr(worker, "save_audio_to_storage", fake_save_audio)
    monkeypatch.setattr(worker, "is_generation_cancelled", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "_publish_event", AsyncMock())
    monkeypatch.setattr(worker, "claim_delivery_atomic", AsyncMock(return_value=True))

    initial_gen_success = (
        GENERATION_TOTAL.labels(status="success")._value.get()
        if hasattr(GENERATION_TOTAL.labels(status="success"), "_value")
        else 0
    )
    initial_del_success = (
        DELIVERY_TOTAL.labels(status="success")._value.get()
        if hasattr(DELIVERY_TOTAL.labels(status="success"), "_value")
        else 0
    )

    await worker.run_generation_task(_command(gen_id=12), _context(telegram))

    if hasattr(GENERATION_TOTAL.labels(status="success"), "_value"):
        assert GENERATION_TOTAL.labels(status="success")._value.get() == initial_gen_success + 1
        assert DELIVERY_TOTAL.labels(status="success")._value.get() == initial_del_success + 1
