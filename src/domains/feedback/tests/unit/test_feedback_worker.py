"""Тесты воркера запроса обратной связи после оценки."""

import logging
from unittest.mock import AsyncMock

import pytest
from taskiq import TaskiqState

from domains.evaluation.evaluation_messages import FEEDBACK_CHOICE_TEXT
from domains.feedback import worker as feedback_worker
from domains.feedback.fsm import FeedbackStates
from domains.feedback.keyboards import get_feedback_keyboard
from shared.contracts.events import EvaluationCompleted
from shared.ports.fake_telegram import FakeTelegramPort


@pytest.fixture
def fake_bot():
    """Фейковый бот с id для StorageKey."""

    class FakeBot:
        id = 123456789

    return FakeBot()


@pytest.fixture
def fake_storage():
    """Фейковое хранилище FSM с проверкой вызовов."""

    class FakeStorage:
        """Фейковое хранилище FSM с проверкой вызовов."""

        def __init__(self):
            self.states = {}
            self.data = {}

        async def set_state(self, key, state):
            self.states[key] = state

        async def set_data(self, key, data):
            self.data[key] = data

        async def update_data(self, key, data):
            if key not in self.data:
                self.data[key] = {}
            self.data[key].update(data)
            return self.data[key]

    return FakeStorage()


@pytest.fixture
def taskiq_state(fake_bot, fake_storage):
    """TaskIQ state с фейковыми зависимостями."""
    state = TaskiqState()
    state["bot"] = fake_bot
    state["storage"] = fake_storage
    state["telegram_port"] = FakeTelegramPort()
    return state


async def test_request_feedback_handler_sends_message_and_sets_fsm(taskiq_state):
    """Воркер отправляет запрос отзыва и устанавливает FSM в waiting_for_feedback_choice."""
    event = EvaluationCompleted(user_id=42, chat_id=42, gen_id=10)

    await feedback_worker.request_feedback_handler(event=event, state=taskiq_state)

    telegram_port: FakeTelegramPort = taskiq_state["telegram_port"]
    assert len(telegram_port.sent_messages) == 1
    assert telegram_port.sent_messages[0]["chat_id"] == 42
    assert telegram_port.sent_messages[0]["text"] == FEEDBACK_CHOICE_TEXT
    assert telegram_port.sent_messages[0]["kwargs"]["reply_markup"] == get_feedback_keyboard()

    storage = taskiq_state["storage"]
    assert len(storage.states) == 1
    stored_state = list(storage.states.values())[0]
    assert stored_state == FeedbackStates.waiting_for_feedback_choice

    assert len(storage.data) == 1
    stored_data = list(storage.data.values())[0]
    assert stored_data["feedback_evaluation"] is None
    assert stored_data["feedback_text"] is None
    assert stored_data["feedback_prompt_message_id"] is None


async def test_request_feedback_handler_works_for_different_users(taskiq_state):
    """Воркер корректно обрабатывает события от разных пользователей."""
    event1 = EvaluationCompleted(user_id=1, chat_id=1, gen_id=5)
    event2 = EvaluationCompleted(user_id=2, chat_id=2, gen_id=6)

    await feedback_worker.request_feedback_handler(event=event1, state=taskiq_state)
    await feedback_worker.request_feedback_handler(event=event2, state=taskiq_state)

    telegram_port: FakeTelegramPort = taskiq_state["telegram_port"]
    assert len(telegram_port.sent_messages) == 2
    assert telegram_port.sent_messages[0]["chat_id"] == 1
    assert telegram_port.sent_messages[1]["chat_id"] == 2

    storage = taskiq_state["storage"]
    assert len(storage.states) == 2


async def test_request_feedback_handler_logs_success(taskiq_state, caplog):
    """Воркер логирует успешную отправку запроса отзыва."""
    caplog.set_level(logging.INFO, logger=feedback_worker.log.name)
    event = EvaluationCompleted(user_id=99, chat_id=99, gen_id=20)

    await feedback_worker.request_feedback_handler(event=event, state=taskiq_state)

    assert "Feedback request sent" in caplog.text
    assert "user=99" in caplog.text
    assert "gen_id=20" in caplog.text


async def test_request_feedback_handler_swallows_telegram_error(taskiq_state, caplog, monkeypatch):
    """Сбой отправки сообщения логируется и не пробрасывается наружу."""
    caplog.set_level(logging.ERROR, logger=feedback_worker.log.name)

    async def fail_send_message(*args, **kwargs):
        raise RuntimeError("telegram api error")

    telegram_port: FakeTelegramPort = taskiq_state["telegram_port"]
    telegram_port.send_message = fail_send_message

    notify_calls = []

    async def fake_notify_owner(context: str):
        notify_calls.append(context)

    monkeypatch.setattr(feedback_worker, "notify_owner", fake_notify_owner)

    event = EvaluationCompleted(user_id=77, chat_id=77, gen_id=15)
    await feedback_worker.request_feedback_handler(event=event, state=taskiq_state)

    assert "Failed to request feedback" in caplog.text
    assert "user=77" in caplog.text
    assert "gen_id=15" in caplog.text
    assert len(notify_calls) == 1
    assert "user=77" in notify_calls[0]
    assert "gen_id=15" in notify_calls[0]


async def test_request_feedback_handler_swallows_fsm_error(taskiq_state, caplog, monkeypatch):
    """Сбой записи FSM логируется и не пробрасывается наружу."""
    caplog.set_level(logging.ERROR, logger=feedback_worker.log.name)
    taskiq_state["storage"].set_state = AsyncMock(side_effect=RuntimeError("fsm error"))

    event = EvaluationCompleted(user_id=88, chat_id=88, gen_id=25)
    await feedback_worker.request_feedback_handler(event=event, state=taskiq_state)

    assert "Failed to request feedback" in caplog.text
