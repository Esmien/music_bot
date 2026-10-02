"""Юнит-тесты хендлеров оценки композиции."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, User

from domains.evaluation.handlers import handle_evaluate, handle_evaluate_prompt
from domains.feedback.fsm import FeedbackStates


def test_feedback_states_canonical_import() -> None:
    """Проверяет доступность состояний FeedbackStates из канонического модуля."""
    assert hasattr(FeedbackStates, "waiting_evaluation")
    assert hasattr(FeedbackStates, "waiting_for_feedback_choice")
    assert hasattr(FeedbackStates, "waiting_feedback")


@pytest.mark.asyncio
async def test_handle_evaluate_prompt(make_message, fake_state) -> None:
    """Хендлер повторно отправляет клавиатуру оценки при текстовом вводе."""
    state = fake_state()
    await state.update_data(gen_id=42)
    message = make_message(text="Привет")

    await handle_evaluate_prompt(message=message, state=state)

    assert len(message.answers) == 1
    assert "Оцените" in message.answers[0]


@pytest.mark.asyncio
async def test_handle_evaluate_like(monkeypatch: pytest.MonkeyPatch, fake_state) -> None:
    """Обработка положительной оценки обновляет FSM и сохраняет фидбек."""
    save_feedback_mock = AsyncMock()
    monkeypatch.setattr("domains.evaluation.handlers.save_feedback", save_feedback_mock)

    state = fake_state()
    await state.update_data(gen_id=10)

    user = User(id=123, is_bot=False, first_name="Test")
    message = MagicMock()
    message.edit_reply_markup = AsyncMock()
    message.answer = AsyncMock()

    callback = MagicMock(spec=CallbackQuery)
    callback.data = "fb:like:10"
    callback.from_user = user
    callback.message = message
    callback.answer = AsyncMock()

    await handle_evaluate(callback=callback, state=state)

    save_feedback_mock.assert_awaited_once_with(gen_id=10, user_id=123, evalue=True)
    assert state.state == FeedbackStates.waiting_for_feedback_choice
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_evaluate_gen_id_mismatch(monkeypatch: pytest.MonkeyPatch, fake_state) -> None:
    """Оценка с несовпадающим gen_id отклоняется предупреждением."""
    save_feedback_mock = AsyncMock()
    monkeypatch.setattr("domains.evaluation.handlers.save_feedback", save_feedback_mock)

    state = fake_state()
    await state.update_data(gen_id=20)

    user = User(id=123, is_bot=False, first_name="Test")
    callback = MagicMock(spec=CallbackQuery)
    callback.data = "fb:like:10"
    callback.from_user = user
    callback.answer = AsyncMock()

    await handle_evaluate(callback=callback, state=state)

    save_feedback_mock.assert_not_called()
    callback.answer.assert_awaited_once_with(
        text="Эта оценка относится к устаревшей генерации.",
        show_alert=True,
    )
