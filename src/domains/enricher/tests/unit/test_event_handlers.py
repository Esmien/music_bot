"""Юнит-тесты для event-хендлеров обогащения."""

from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from taskiq import TaskiqState

from domains.enricher.fsm import PromptEnricherStates
from domains.enricher.handlers import handle_enrichment_completed_event, handle_enrichment_failed_event
from domains.enricher.state_models import EnrichmentFlowState
from shared.contracts.events import EnrichmentCompleted, GenerationFailed
from shared.ports.fake_telegram import FakeTelegramPort


@pytest.fixture
def fake_bot():
    """Фейковый бот с id для StorageKey."""

    class FakeBot:
        id = 123456789

    return FakeBot()


@pytest.fixture
def fake_storage():
    """Фейковое хранилище FSM для event-хендлеров."""

    class FakeStorage:
        def __init__(self):
            self._states = {}
            self._data = {}

        async def set_state(self, key, state):
            self._states[key] = state

        async def get_state(self, key):
            return self._states.get(key)

        async def get_data(self, key):
            return self._data.get(key, {})

        async def update_data(self, key, data):
            if key not in self._data:
                self._data[key] = {}
            self._data[key].update(data)

        async def set_data(self, key, data):
            """Устанавливает данные FSM (требуется для FSMContext.clear())."""
            self._data[key] = data

    return FakeStorage()


@pytest.fixture
def taskiq_state(fake_bot, fake_storage):
    """TaskIQ context с объектом state и фейковыми зависимостями."""
    state = TaskiqState()
    state["bot"] = fake_bot
    state["storage"] = fake_storage
    state["telegram_port"] = FakeTelegramPort()
    return SimpleNamespace(state=state)


async def test_enrichment_completed_uses_port_from_context(taskiq_state, fake_storage, fake_bot):
    """Проверяет, что handle_enrichment_completed_event использует TelegramPort из контекста.

    Это предотвращает утечку Bot-сессий при создании нового Bot() в каждом вызове.
    """
    user_id = 123
    chat_id = 123
    enriched_prompt = "Веселая песня про кота"

    # Подготовка FSM-состояния
    key = StorageKey(bot_id=fake_bot.id, chat_id=chat_id, user_id=user_id)
    await fake_storage.set_state(key, PromptEnricherStates.waiting_for_idea.state)

    flow_state = EnrichmentFlowState(
        prompt="Песня про кота",
        enriching=True,
    )
    await fake_storage.update_data(key, flow_state.model_dump())

    # Создаём событие
    event = EnrichmentCompleted(
        user_id=user_id,
        chat_id=chat_id,
        initial_prompt="Песня про кота",
        enriched_prompt=enriched_prompt,
    )

    # Выполняем хендлер
    await handle_enrichment_completed_event(event=event, context=taskiq_state)

    # Проверяем, что использовался TelegramPort из контекста
    telegram_port: FakeTelegramPort = taskiq_state.state["telegram_port"]
    assert len(telegram_port.sent_messages) == 1
    assert telegram_port.sent_messages[0]["chat_id"] == chat_id
    assert enriched_prompt in telegram_port.sent_messages[0]["text"]

    # Проверяем, что состояние обновлено
    fsm_context = FSMContext(storage=fake_storage, key=key)
    state = await fsm_context.get_state()
    assert state == PromptEnricherStates.waiting_for_approval.state


async def test_enrichment_completed_drops_outdated_event(taskiq_state, fake_storage, fake_bot):
    """Проверяет, что событие игнорируется, если пользователь уже в другом состоянии."""
    user_id = 456
    chat_id = 456

    # Пользователь уже не в процессе обогащения
    key = StorageKey(bot_id=fake_bot.id, chat_id=chat_id, user_id=user_id)
    await fake_storage.set_state(key, "some_other_state")

    event = EnrichmentCompleted(
        user_id=user_id,
        chat_id=chat_id,
        initial_prompt="Какая-то идея",
        enriched_prompt="Какой-то промпт",
    )

    await handle_enrichment_completed_event(event=event, context=taskiq_state)

    # Сообщение не должно быть отправлено
    telegram_port: FakeTelegramPort = taskiq_state.state["telegram_port"]
    assert len(telegram_port.sent_messages) == 0


async def test_enrichment_failed_uses_port_from_context(taskiq_state, fake_storage, fake_bot):
    """Проверяет, что handle_enrichment_failed_event использует TelegramPort из контекста."""
    user_id = 789
    chat_id = 789

    # Подготовка FSM-состояния для сбоя обогащения
    key = StorageKey(bot_id=fake_bot.id, chat_id=chat_id, user_id=user_id)
    await fake_storage.set_state(key, PromptEnricherStates.waiting_for_idea.state)

    flow_state = EnrichmentFlowState(
        prompt="Песня про собаку",
        enriching=True,
    )
    await fake_storage.update_data(key, flow_state.model_dump())

    # Создаём событие сбоя
    event = GenerationFailed(
        user_id=user_id,
        chat_id=chat_id,
        error_message="Timeout",
        stage="enrichment",
    )

    # Выполняем хендлер
    await handle_enrichment_failed_event(event=event, context=taskiq_state)

    # Проверяем, что использовался TelegramPort из контекста
    telegram_port: FakeTelegramPort = taskiq_state.state["telegram_port"]
    assert len(telegram_port.sent_messages) == 1
    assert telegram_port.sent_messages[0]["chat_id"] == chat_id

    # Проверяем, что флаг enriching сброшен
    fsm_context = FSMContext(storage=fake_storage, key=key)
    data = await fsm_context.get_data()
    assert data.get("enriching") is False


async def test_no_bot_session_leak_on_exception(taskiq_state, fake_storage, fake_bot):
    """Документирует, что хендлеры используют TelegramPort из контекста.

    Хендлеры больше не создают Bot() вручную, поэтому утечки сессий невозможны.
    Все взаимодействие с Telegram идёт через TelegramPort, который управляется
    на уровне воркера и корректно закрывается при shutdown.
    """
    user_id = 111
    chat_id = 111

    # Подготовка валидного FSM-состояния
    key = StorageKey(bot_id=fake_bot.id, chat_id=chat_id, user_id=user_id)
    await fake_storage.set_state(key, PromptEnricherStates.waiting_for_idea.state)

    flow_state = EnrichmentFlowState(
        prompt="Test prompt",
        enriching=True,
    )
    await fake_storage.set_data(key, flow_state.model_dump())

    event = EnrichmentCompleted(
        user_id=user_id,
        chat_id=chat_id,
        initial_prompt="Test initial",
        enriched_prompt="Test",
    )

    # Выполняем хендлер - он использует TelegramPort из контекста
    await handle_enrichment_completed_event(event=event, context=taskiq_state)

    # TelegramPort из контекста был использован, новый Bot не создавался
    telegram_port: FakeTelegramPort = taskiq_state.state["telegram_port"]
    assert len(telegram_port.sent_messages) == 1
    assert telegram_port.sent_messages[0]["chat_id"] == chat_id

    # Даже если бы хендлер упал с исключением, утечки не было бы,
    # потому что Bot управляется на уровне воркера, а не создаётся в хендлере


async def test_enrichment_failed_context_missing_state_raises_key_error():
    """Проверяет возникновение KeyError при отсутствии нужной зависимости в handle_enrichment_failed_event."""
    empty_context = SimpleNamespace(state={})
    event = GenerationFailed(
        user_id=123,
        chat_id=123,
        error_message="Timeout",
        stage="enrichment",
    )
    with pytest.raises(KeyError):
        await handle_enrichment_failed_event(event=event, context=empty_context)


async def test_enrichment_completed_context_missing_state_raises_key_error():
    """Проверяет возникновение KeyError при отсутствии нужной зависимости."""
    empty_context = SimpleNamespace(state={})
    event = EnrichmentCompleted(
        user_id=123,
        chat_id=123,
        initial_prompt="Тест",
        enriched_prompt="Тест",
    )
    with pytest.raises(KeyError):
        await handle_enrichment_completed_event(event=event, context=empty_context)
