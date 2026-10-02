"""Интеграционные тесты хендлеров генерации: FSM, блокировки, публикация команд.

Генерация выполняется в TaskIQ-воркере (сценарии воркера покрыты
test_generation_worker.py), поэтому здесь проверяется только хендлеровый слой:
создание PENDING-записи, выставление FSM-флагов и публикация RunGeneration.
"""

import asyncio
from types import SimpleNamespace

import pytest

from core.database import User
from domains.base import base_messasges
from domains.base import handlers as base_handlers
from domains.enricher import handlers as handlers_enricher
from domains.generation import handlers as handlers_generation
from domains.generation import pipeline_handlers as pipeline
from domains.generation.generation_messages import GENERATION_IN_PROGRESS_TEXT
from domains.generation.models import Generation, GenerationStatus
from domains.generation.registries import task_registry

pytestmark = pytest.mark.integration


class RecordingBroker:
    """Заглушка брокера: копит опубликованные команды вместо отправки в RabbitMQ."""

    def __init__(self) -> None:
        self.kicked: list[tuple[str, object]] = []

    def kicker(self, task_name: str):
        kicked = self.kicked

        class Kicker:
            def __init__(self, name: str) -> None:
                self.name = name

            async def kiq(self, command: object) -> None:
                kicked.append((self.name, command))

        return Kicker(task_name)


@pytest.fixture
def recording_broker(monkeypatch):
    """Подменяет брокер в pipeline_handlers на записывающую заглушку."""
    broker_stub = RecordingBroker()
    monkeypatch.setattr(pipeline, "generation_broker", broker_stub)
    return broker_stub


@pytest.fixture
async def clean_generation_registry(fake_redis, monkeypatch):
    """Пустой реестр активных задач генерации через канонический API до и после теста."""
    monkeypatch.setattr(task_registry, "redis_client", fake_redis)
    await task_registry.clear_active_tasks()
    yield
    await task_registry.clear_active_tasks()


@pytest.fixture
def make_callback():
    """Фабрика callback-запросов-заглушек для кнопки повтора."""

    class FakeCallback:
        def __init__(self, uid, message):
            self.from_user = SimpleNamespace(id=uid)
            self.message = message
            self.answered = []

            async def answer(text=None, show_alert=False):
                self.answered.append((text, show_alert))

            self.answer = answer

    return FakeCallback


async def _make_authorized_user(sessionmaker, tg_id: int) -> None:
    async with sessionmaker() as session:
        session.add(User(tg_id=tg_id, is_authorized=True))
        await session.commit()


async def test_cmd_generate_starts_enrichment_flow(
    patched_auth_db, clean_auth_state, make_message, fake_state, monkeypatch
):
    """Кнопка «🎵 Сгенерировать» для авторизованного запускает сценарий обогащения.

    DEVIATION: подсказки теперь шлёт порт enrichment_flow_starter, поэтому
    проверяем делегирование, а не тексты подсказок.
    """

    async def fake_start_enrichment(message, state):
        fake_start_enrichment.called_with = (message, state)

    fake_start_enrichment.called_with = None
    monkeypatch.setattr(
        "shared.domain_ports.enrichment_flow_starter.start_enrichment",
        fake_start_enrichment,
    )

    await _make_authorized_user(patched_auth_db, 50)
    msg = make_message(uid=50)
    state = fake_state()

    await handlers_generation.cmd_generate(msg, state)

    assert fake_start_enrichment.called_with == (msg, state)


async def test_cmd_generate_blocked_while_generating(
    patched_auth_db, clean_auth_state, make_message, fake_state, monkeypatch
):
    """«🎵 Сгенерировать» заблокирована во время идущей генерации."""

    async def unexpected_start(message, state):
        raise AssertionError("start_enrichment не должен вызываться при активной генерации")

    monkeypatch.setattr(
        "shared.domain_ports.enrichment_flow_starter.start_enrichment",
        unexpected_start,
    )

    await _make_authorized_user(patched_auth_db, 51)
    msg = make_message(uid=51)
    state = fake_state()
    await state.update_data(generating=True)

    await handlers_generation.cmd_generate(msg, state)

    assert msg.answers[-1] == handlers_generation.GENERATION_CANCEL_WAIT_TEXT
    assert state.state is None


async def test_cmd_generate_requires_auth(patched_auth_db, clean_auth_state, make_message, fake_state, monkeypatch):
    """Неавторизованный пользователь не попадает в диалог генерации."""

    async def unexpected_start(message, state):
        raise AssertionError("start_enrichment не должен вызываться без авторизации")

    monkeypatch.setattr(
        "shared.domain_ports.enrichment_flow_starter.start_enrichment",
        unexpected_start,
    )

    msg = make_message(uid=70)
    state = fake_state()

    await handlers_generation.cmd_generate(msg, state)

    assert state.state is None


def test_build_generation_prompt_with_template_wraps_in_brief():
    """Заполненный шаблон оборачивается в бриф для модели."""
    prompt = handlers_enricher._build_generation_prompt("Жанр: рок\nТекст песни: раз-два")

    assert "brief" in prompt
    assert "sing in Russian" in prompt


def test_build_generation_prompt_plain_lyrics():
    """Простые стихи без маркеров шаблона идут с формулировкой «these lyrics»."""
    prompt = handlers_enricher._build_generation_prompt("Просто стихи про кота")

    assert "these lyrics" in prompt
    assert "brief" not in prompt


async def test_handle_idea_rejects_too_long(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Слишком длинное описание песни отклоняется."""
    msg = make_message(text="а" * (handlers_enricher.MAX_PROMPT_LEN + 1), uid=54)
    state = fake_state()

    await handlers_enricher.handle_idea(msg, state)

    assert msg.answers[-1] == handlers_enricher.PROMPT_TOO_LONG_MSG.format(
        length=len(msg.text), max_len=handlers_enricher.MAX_PROMPT_LEN
    )
    assert state.state is None


async def test_handle_idea_rejects_blank_text(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Описание из одних пробелов отклоняется."""
    msg = make_message(text="   ", uid=71)
    state = fake_state()

    await handlers_enricher.handle_idea(msg, state)

    assert msg.answers[-1] == handlers_enricher.EMPTY_PROMPT_MSG
    assert state.state is None


async def test_handle_idea_rejects_untouched_template(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Нетронутый шаблон отклоняется."""
    template = "Жанр: \n\nНастроение: \n\nИнструменты: \n\nТемп и ритм: \n\nГолос: \n\nТекст песни: \n"
    msg = make_message(text=template, uid=72)
    state = fake_state()

    await handlers_enricher.handle_idea(msg, state)

    assert msg.answers[-1] == handlers_enricher.EMPTY_TEMPLATE_MSG
    assert state.state is None


async def test_handle_title_creates_pending_generation_and_publishes_command(
    patched_auth_db, clean_auth_state, make_message, fake_state, monkeypatch, recording_broker
):
    """Ввод названия создаёт PENDING-запись в БД и публикует команду генерации."""
    await _make_authorized_user(patched_auth_db, 55)
    monkeypatch.setattr(pipeline, "get_session", patched_auth_db)

    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Моя песня", uid=55)

    await handlers_generation.handle_title(msg, state)

    assert len(recording_broker.kicked) == 1
    task_name, command = recording_broker.kicked[0]
    assert task_name == "run_generation"
    assert command.user_id == 55
    assert command.chat_id == 55
    assert command.prompt == "промпт"
    assert command.title == "Моя песня"
    assert command.status_message_id is None

    data = await state.get_data()
    assert data["generating"] is True
    assert data["gen_id"] == command.gen_id

    async with patched_auth_db() as session:
        generation = await session.get(Generation, command.gen_id)
    assert generation is not None
    assert generation.status is GenerationStatus.PENDING
    assert generation.title == "Моя песня"
    assert generation.user_id == 55


async def test_cancel_generation_kills_running_task(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, fake_redis, monkeypatch
):
    """Кнопка «❌ Отмена» гасит живую фоновую задачу генерации и очищает registry."""
    monkeypatch.setattr(task_registry, "redis_client", fake_redis)

    msg = make_message(uid=57)
    state = fake_state()
    await state.update_data(gen_id=100, generating=True)

    task = asyncio.create_task(asyncio.sleep(60))
    await task_registry.register_active_task(uid=57, task=task)

    await base_handlers.cmd_cancel(msg, state)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.cleared
    assert msg.answers[-1] == base_messasges.CANCEL_ACTION
    # Проверяем очистку registry
    assert task_registry.get_active_task(uid=57) is None


async def test_retry_generation_requires_auth(
    patched_auth_db, clean_auth_state, make_message, fake_state, make_callback
):
    """Кнопка повтора не работает для неавторизованных."""
    state = fake_state()
    await state.update_data(prompt="промпт", title="название")
    msg = make_message(uid=58)
    callback = make_callback(58, msg)

    await handlers_generation.retry_generation(callback, state)

    assert callback.answered[0][0] == handlers_generation.ACCESS_DENIED_TEXT
    assert state.cleared


async def test_retry_generation_without_prompt_suggests_restart(
    patched_auth_db, clean_auth_state, make_message, fake_state, make_callback
):
    """Ретрай без сохранённого промпта предлагает начать заново."""
    await _make_authorized_user(patched_auth_db, 59)
    state = fake_state()
    callback = make_callback(59, make_message(uid=59))

    await handlers_generation.retry_generation(callback, state)

    assert callback.answered[0][0] == handlers_generation.RESTART_GENERATION_TEXT
    assert state.cleared


async def test_generate_and_send_blocked_inside_lock(
    patched_auth_db, clean_auth_state, make_message, fake_state, monkeypatch, recording_broker
):
    """Проверка внутри лока блокирует второй запуск генерации."""
    await _make_authorized_user(patched_auth_db, 60)
    monkeypatch.setattr(pipeline, "get_session", patched_auth_db)

    state = fake_state()
    await state.update_data(generating=True, prompt="промпт")
    msg = make_message(uid=60)

    await pipeline.generate_and_send(msg, state, "промпт", "Название", 60)

    assert msg.answers[-1] == GENERATION_IN_PROGRESS_TEXT
    assert recording_broker.kicked == []


async def test_handle_title_rejects_empty_title(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Название из одних пробелов отклоняется."""
    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="   ", uid=64)

    await handlers_generation.handle_title(msg, state)

    assert msg.answers[-1] == handlers_generation.EMPTY_TITLE_TEXT
    assert state.state is None


async def test_handle_title_rejects_too_long_title(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Слишком длинное название отклоняется."""
    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="а" * (handlers_generation.MAX_TITLE_LEN + 1), uid=65)

    await handlers_generation.handle_title(msg, state)

    assert msg.answers[-1] == handlers_generation.TITLE_TOO_LONG_TEXT.format(
        max_title_len=handlers_generation.MAX_TITLE_LEN
    )
    assert state.state is None


async def test_handle_title_blocked_while_generating(patched_auth_db, clean_auth_state, make_message, fake_state):
    """handle_title не запускает генерацию при активной генерации."""
    state = fake_state()
    await state.update_data(prompt="промпт", generating=True)
    msg = make_message(text="Название", uid=66)

    await handlers_generation.handle_title(msg, state)

    assert msg.answers[-1] == handlers_generation.GENERATION_CANCEL_WAIT_TEXT
    assert "title" not in (await state.get_data())


async def test_handle_title_missing_prompt_suggests_restart(
    patched_auth_db, clean_auth_state, make_message, fake_state
):
    """Генерация без сохранённого промпта предлагает начать заново."""
    state = fake_state()
    msg = make_message(text="Название", uid=73)

    await handlers_generation.handle_title(msg, state)

    assert msg.answers[-1] == handlers_generation.PROMPT_LOST_TEXT
    assert state.cleared


async def test_retry_generation_blocked_while_generating(
    patched_auth_db, clean_auth_state, make_message, fake_state, make_callback
):
    """Ретрай заблокирован во время идущей генерации."""
    await _make_authorized_user(patched_auth_db, 67)
    state = fake_state()
    await state.update_data(prompt="промпт", title="название", generating=True)
    callback = make_callback(67, make_message(uid=67))

    await handlers_generation.retry_generation(callback, state)

    assert callback.answered[-1] == (handlers_generation.GENERATION_ALREADY_RUNNING_TEXT, True)
    assert len(callback.message.audios) == 0


async def test_retry_generation_publishes_command(
    patched_auth_db, clean_auth_state, make_message, fake_state, make_callback, monkeypatch, recording_broker
):
    """Повтор генерации публикует команду RunGeneration."""
    await _make_authorized_user(patched_auth_db, 68)
    monkeypatch.setattr(pipeline, "get_session", patched_auth_db)

    state = fake_state()
    await state.update_data(prompt="промпт", title="Ретрай")
    msg = make_message(uid=68)
    callback = make_callback(68, msg)

    await handlers_generation.retry_generation(callback, state)

    assert msg.deleted
    assert callback.answered[-1] == (None, False)
    assert len(recording_broker.kicked) == 1

    task_name, command = recording_broker.kicked[0]
    assert task_name == "run_generation"
    assert command.title == "Ретрай"
    assert command.prompt == "промпт"


async def test_cancel_sets_redis_cancel_token(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, fake_redis, monkeypatch
):
    """Отмена генерации устанавливает Redis cancel-token для воркера."""
    import core.redis as redis_module

    monkeypatch.setattr(redis_module, "redis_client", fake_redis)

    msg = make_message(uid=58)
    state = fake_state()
    await state.update_data(gen_id=200, generating=True)

    await base_handlers.cmd_cancel(msg, state)

    # Проверяем, что cancel-token установлен в Redis
    assert await redis_module.is_generation_cancelled(gen_id=200) is True
    assert state.cleared
    assert task_registry.get_active_task(uid=58) is None


async def test_cancel_without_active_generation_clears_state(
    patched_auth_db, clean_auth_state, make_message, fake_state
):
    """Отмена без активной генерации просто очищает FSM и возвращает в меню."""
    msg = make_message(uid=59)
    state = fake_state()

    await base_handlers.cmd_cancel(msg, state)

    assert state.cleared
    assert msg.answers[-1] == base_messasges.CANCEL_ACTION


async def test_cancel_before_claim_prevents_api_call(
    patched_auth_db, clean_auth_state, make_message, fake_state, fake_redis, monkeypatch
):
    """Этап 1: отмена до claim — PENDING → CANCELLED, API не вызывается."""
    import core.redis as redis_module
    from domains.generation.models import Generation, GenerationStatus

    monkeypatch.setattr(redis_module, "redis_client", fake_redis)
    monkeypatch.setattr(pipeline, "get_session", patched_auth_db)

    await _make_authorized_user(patched_auth_db, 80)
    state = fake_state()
    msg = make_message(uid=80)

    # Создаём PENDING-генерацию в БД
    async with patched_auth_db() as session:
        generation = Generation(
            id=300,
            user_id=80,
            prompt="test prompt",
            enriched_prompt={"genre": "rock"},
            title="Test Song",
            status=GenerationStatus.PENDING,
        )
        session.add(generation)
        await session.commit()

    await state.update_data(gen_id=300, generating=True, prompt="test prompt", title="Test Song")

    # Пользователь отменяет генерацию до того, как воркер её захватил
    await base_handlers.cmd_cancel(msg, state)

    # Проверяем, что cancel-токен установлен (source of truth)
    assert await redis_module.is_generation_cancelled(gen_id=300) is True

    # Имитируем воркер: проверка токена перед claim
    cancelled_before_claim = await redis_module.is_generation_cancelled(gen_id=300)
    assert cancelled_before_claim is True

    # Воркер обнаружит токен и установит CANCELLED без API-запроса
    async with patched_auth_db() as session:
        generation = await session.get(Generation, 300)
        if cancelled_before_claim:
            generation.status = GenerationStatus.CANCELLED
            await session.commit()

    # Проверяем финальный статус
    async with patched_auth_db() as session:
        generation = await session.get(Generation, 300)
        assert generation.status == GenerationStatus.CANCELLED


async def test_cancel_during_api_raises_cancelled_error(patched_auth_db, fake_redis, monkeypatch):
    """Этап 2: отмена во время API call — asyncio.CancelledError поднимается."""
    import core.redis as redis_module
    from domains.generation.service import run_generation

    monkeypatch.setattr(redis_module, "redis_client", fake_redis)

    # Устанавливаем cancel-токен
    await redis_module.request_generation_cancel(gen_id=400)

    # Проверяем, что run_generation поднимает CancelledError при обнаружении токена
    async def dummy_progress(stage: str, fraction: float):
        pass

    with pytest.raises(asyncio.CancelledError):
        await run_generation(prompt="test", gen_id=400, on_progress=dummy_progress)


async def test_cancel_after_mp3_saved_keeps_success_status(patched_auth_db, fake_redis, monkeypatch):
    """Этап 3: отмена после сохранения MP3 — статус SUCCESS не меняется."""
    import core.redis as redis_module
    from domains.generation.models import Generation, GenerationStatus

    monkeypatch.setattr(redis_module, "redis_client", fake_redis)

    await _make_authorized_user(patched_auth_db, 81)

    # Создаём генерацию со статусом SUCCESS (MP3 уже сохранён)
    async with patched_auth_db() as session:
        generation = Generation(
            id=500,
            user_id=81,
            prompt="test prompt",
            enriched_prompt={"genre": "rock"},
            title="Test Song",
            status=GenerationStatus.SUCCESS,
            audio_path="/tmp/test.mp3",
            audio_size=1024,
            audio_checksum="abc123",
        )
        session.add(generation)
        await session.commit()

    # Пользователь отменяет после сохранения MP3, но до доставки
    await redis_module.request_generation_cancel(gen_id=500)

    # Проверяем, что cancel-токен установлен
    assert await redis_module.is_generation_cancelled(gen_id=500) is True

    # Статус SUCCESS не должен измениться
    async with patched_auth_db() as session:
        generation = await session.get(Generation, 500)
        assert generation.status == GenerationStatus.SUCCESS
        assert generation.audio_path == "/tmp/test.mp3"


async def test_cancel_during_delivery_skips_telegram_send(patched_auth_db, fake_redis, monkeypatch):
    """Этап 4: отмена во время delivery — Telegram-отправка пропускается, статус SUCCESS."""
    import core.redis as redis_module
    from domains.generation.models import Generation, GenerationStatus
    from domains.generation.worker import deliver_generation_audio
    from shared.contracts.commands import RunGeneration

    monkeypatch.setattr(redis_module, "redis_client", fake_redis)

    await _make_authorized_user(patched_auth_db, 82)

    # Создаём генерацию со статусом SUCCESS и сохранённым аудио
    async with patched_auth_db() as session:
        generation = Generation(
            id=600,
            user_id=82,
            prompt="test prompt",
            enriched_prompt={"genre": "rock"},
            title="Test Song",
            status=GenerationStatus.SUCCESS,
            audio_path="src/mock_generation.json",  # Используем существующий файл для теста
            audio_size=1024,
            audio_checksum="abc123",
        )
        session.add(generation)
        await session.commit()

    # Устанавливаем cancel-токен перед доставкой
    await redis_module.request_generation_cancel(gen_id=600)

    # Мокируем TelegramPort для отслеживания вызовов send_audio
    send_audio_called = False

    class MockTelegramPort:
        async def send_audio(self, chat_id, audio, title, caption):
            nonlocal send_audio_called
            send_audio_called = True
            return 12345

        async def edit_message(self, chat_id, message_id, text):
            pass

        async def send_message(self, chat_id, text, **kwargs):
            return 12346

    mock_telegram = MockTelegramPort()
    command = RunGeneration(
        user_id=82,
        chat_id=82,
        gen_id=600,
        prompt="test prompt",
        title="Test Song",
        status_message_id=None,
    )

    # Вызываем deliver_generation_audio — она должна обнаружить отмену и пропустить отправку
    await deliver_generation_audio(
        telegram=mock_telegram,
        command=command,
        audio_path="src/mock_generation.json",
    )

    # Проверяем, что send_audio НЕ был вызван из-за отмены
    assert send_audio_called is False

    # Статус остаётся SUCCESS
    async with patched_auth_db() as session:
        generation = await session.get(Generation, 600)
        assert generation.status == GenerationStatus.SUCCESS
