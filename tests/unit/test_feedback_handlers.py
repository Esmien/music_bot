"""Тесты хендлеров сценария фидбека после генерации.

Внешний сервис сохранения фидбека мокается, потому что проверяется
поведение хендлеров, FSM и пользовательских сообщений, а не БД.
"""

from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from core.config import UIConfig, settings
from domains.base.keyboards import get_main_keyboard
from domains.evaluation.fsm import FeedbackStates
from handlers import feedback_handlers
from keyboards.feedback_keyboards import get_feedback_finish_keyboard, get_feedback_keyboard


def _inline_button_texts(markup: InlineKeyboardMarkup | None) -> list[list[str | None]]:
    """Возвращает callback_data кнопок inline-клавиатуры.

    Args:
        markup: Проверяемая inline-клавиатура.

    Returns:
        Список рядов кнопок с их callback_data.
    """
    if markup is None:
        return []
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def _reply_button_texts(markup: ReplyKeyboardMarkup | None) -> list[list[str | None]]:
    """Возвращает тексты кнопок reply-клавиатуры.

    Args:
        markup: Проверяемая reply-клавиатура.

    Returns:
        Список рядов кнопок с их текстами.
    """
    if markup is None:
        return []
    return [[button.text for button in row] for row in markup.keyboard]


@pytest.fixture
def make_feedback_message(make_message):
    """Фабрика сообщений для хендлеров фидбека.

    Добавляет к FakeMessage из conftest сохранение reply_markup в answer
    и заглушку message.bot.edit_reply_markup, которую использует хендлер.
    """

    class FeedbackMessage(make_message):
        def __init__(self, text: str | None = None, uid: int = 1) -> None:
            super().__init__(text=text, uid=uid)
            self.answered_markups: list[ReplyKeyboardMarkup | InlineKeyboardMarkup | None] = []
            self.edited_reply_markups: list[tuple[int | None, int | None, InlineKeyboardMarkup | None]] = []

            async def edit_reply_markup(
                chat_id: int | None = None,
                message_id: int | None = None,
                reply_markup: InlineKeyboardMarkup | None = None,
                **kwargs: object,
            ) -> None:
                self.edited_reply_markups.append((chat_id, message_id, reply_markup))

            self.bot.edit_reply_markup = edit_reply_markup

        async def answer(self, text: str, **kwargs: object) -> SimpleNamespace:
            self.answers.append(text)
            self.answered_markups.append(kwargs.get("reply_markup"))
            sent = SimpleNamespace(text=text, edits=[], deleted=False, bot=self.bot)

            async def edit_text(text: str, **kw: object) -> SimpleNamespace:
                sent.text = text
                sent.edits.append(text)
                return sent

            async def delete() -> None:
                sent.deleted = True

            sent.edit_text = edit_text
            sent.delete = delete
            self.sent.append(sent)
            return sent

    return FeedbackMessage


@pytest.fixture
def make_callback_message(make_feedback_message):
    """Фабрика сообщений-заглушек для callback-запросов фидбека."""

    class CallbackMessage(make_feedback_message):
        def __init__(self, text: str | None = None, uid: int = 1, message_id: int = 123) -> None:
            super().__init__(text=text, uid=uid)
            self.message_id = message_id
            self.edits: list[str] = []
            self.edit_calls: list[tuple[str, InlineKeyboardMarkup | None]] = []
            self.reply_markup_edits: list[InlineKeyboardMarkup | None] = []

        async def edit_text(self, text: str, **kwargs: object) -> object:
            self.text = text
            self.edits.append(text)
            self.edit_calls.append((text, kwargs.get("reply_markup")))
            return self

        async def edit_reply_markup(self, reply_markup: InlineKeyboardMarkup | None = None, **kwargs: object) -> object:
            self.reply_markup_edits.append(reply_markup)
            return self

    return CallbackMessage


@pytest.fixture
def make_callback(make_callback_message):
    """Фабрика callback-запросов-заглушек для сценария фидбека."""

    class FakeCallback:
        def __init__(self, uid: int, message: object | None = None) -> None:
            self.from_user = SimpleNamespace(id=uid)
            self.message = message if message is not None else make_callback_message()
            self.answered: list[tuple[str | None, bool]] = []

        async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
            self.answered.append((text, show_alert))

    return FakeCallback


@pytest.fixture
def save_calls() -> list[tuple[int, str | None, bool]]:
    """Список вызовов save_feedback с аргументами."""
    return []


@pytest.fixture
def patched_save_feedback(monkeypatch, save_calls: list[tuple[int, str | None, bool]]):
    """Подменяет save_feedback на заглушку, собирающую вызовы."""

    async def fake_save_feedback(*, user_id: int, feedback: str | None, evalue: bool) -> None:
        save_calls.append((user_id, feedback, evalue))

    monkeypatch.setattr(feedback_handlers, "save_feedback", fake_save_feedback)
    return save_calls


async def test_handle_feedback_choice_message_reminds_to_use_buttons(make_feedback_message) -> None:
    """Текст на этапе выбора действия напоминает выбрать кнопку."""
    message = make_feedback_message(text="какой-то текст")

    await feedback_handlers.handle_feedback_choice_message(message=message)

    assert message.answers == [UIConfig.FEEDBACK_CHOICE_TEXT]
    assert _inline_button_texts(message.answered_markups[0]) == _inline_button_texts(get_feedback_keyboard())


async def test_handle_feedback_message_saves_text_and_clears_state(
    make_feedback_message,
    fake_state,
    patched_save_feedback,
    monkeypatch,
) -> None:
    """Текст отзыва сохраняется, FSM очищается, клавиатура оценки убирается."""
    monkeypatch.setattr(settings, "MIN_FEEDBACK_TEXT", 5)
    state = fake_state()
    await state.set_state(FeedbackStates.waiting_feedback)
    await state.update_data(feedback_evaluation=True, feedback_prompt_message_id=456)
    message = make_feedback_message(text="  отличный трек  ", uid=77)

    await feedback_handlers.handle_feedback_message(message=message, state=state)

    assert patched_save_feedback == [(77, "отличный трек", True)]
    assert state.cleared is True
    assert message.edited_reply_markups == [(77, 456, None)]
    assert message.answers == [UIConfig.FEEDBACK_THANKS_TEXT]
    assert _reply_button_texts(message.answered_markups[0]) == _reply_button_texts(get_main_keyboard())


async def test_handle_feedback_message_uses_fsm_text_when_message_text_is_none(
    make_feedback_message,
    fake_state,
    patched_save_feedback,
    monkeypatch,
) -> None:
    """Если сообщение без текста, берётся feedback_text из FSM."""
    monkeypatch.setattr(settings, "MIN_FEEDBACK_TEXT", 5)
    state = fake_state()
    await state.update_data(feedback_text="текст из FSM", feedback_evaluation=False, feedback_prompt_message_id=None)
    message = make_feedback_message(text=None, uid=3)

    await feedback_handlers.handle_feedback_message(message=message, state=state)

    assert patched_save_feedback == [(3, "текст из FSM", False)]
    assert message.edited_reply_markups == []


async def test_handle_feedback_message_drops_short_feedback(
    make_feedback_message,
    fake_state,
    patched_save_feedback,
    monkeypatch,
) -> None:
    """Слишком короткий текст не сохраняется как отзыв."""
    monkeypatch.setattr(settings, "MIN_FEEDBACK_TEXT", 5)
    state = fake_state()
    await state.update_data(feedback_evaluation=True, feedback_prompt_message_id=None)
    message = make_feedback_message(text="ок", uid=1)

    await feedback_handlers.handle_feedback_message(message=message, state=state)

    assert patched_save_feedback == [(1, None, True)]


async def test_handle_feedback_message_suppresses_markup_edit_failure(
    make_feedback_message,
    fake_state,
    patched_save_feedback,
    monkeypatch,
) -> None:
    """Сбой уборки клавиатуры не должен ломать завершение сценария."""
    monkeypatch.setattr(settings, "MIN_FEEDBACK_TEXT", 1)
    state = fake_state()
    await state.update_data(feedback_prompt_message_id=999)
    message = make_feedback_message(text="текст", uid=1)

    async def fail_edit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("markup edit failed")

    message.bot.edit_reply_markup = fail_edit

    await feedback_handlers.handle_feedback_message(message=message, state=state)

    assert patched_save_feedback == [(1, "текст", False)]
    assert message.answers == [UIConfig.FEEDBACK_THANKS_TEXT]


async def test_handle_feedback_send_choice_switches_to_waiting_feedback(make_callback, fake_state) -> None:
    """Кнопка «Отправить фидбек» переводит в ожидание текста отзыва."""
    state = fake_state()
    await state.set_state(FeedbackStates.waiting_for_feedback_choice)
    callback = make_callback(uid=10)

    await feedback_handlers.handle_feedback_send_choice(callback=callback, state=state)

    assert callback.message.edit_calls[0][0] == UIConfig.FEEDBACK_PROMPT_TEXT
    assert _inline_button_texts(callback.message.edit_calls[0][1]) == _inline_button_texts(
        get_feedback_finish_keyboard()
    )
    assert state.state == FeedbackStates.waiting_feedback
    data = await state.get_data()
    assert data["feedback_text"] is None
    assert data["feedback_prompt_message_id"] == callback.message.message_id
    assert callback.answered == [(None, False)]


async def test_handle_feedback_send_in_waiting_feedback_shows_prompt_again(make_callback, fake_state) -> None:
    """Повторное нажатие кнопки в ожидании отзыва снова показывает промпт."""
    state = fake_state()
    await state.set_state(FeedbackStates.waiting_feedback)
    await state.update_data(feedback_prompt_message_id=1)
    callback = make_callback(uid=11)

    await feedback_handlers.handle_feedback_send(callback=callback, state=state)

    assert callback.message.edit_calls[0][0] == UIConfig.FEEDBACK_PROMPT_TEXT
    assert state.state == FeedbackStates.waiting_feedback
    data = await state.get_data()
    assert data["feedback_prompt_message_id"] == callback.message.message_id


async def test_handle_feedback_finish_choice_saves_evaluation_from_state(
    make_callback,
    fake_state,
    patched_save_feedback,
    monkeypatch,
) -> None:
    """Завершение на этапе выбора сохраняет оценку и текст из FSM."""
    monkeypatch.setattr(settings, "MIN_FEEDBACK_TEXT", 5)
    state = fake_state()
    await state.set_state(FeedbackStates.waiting_for_feedback_choice)
    await state.update_data(feedback_evaluation=True, feedback_text="нормальный отзыв")
    callback = make_callback(uid=20)

    await feedback_handlers.handle_feedback_finish_choice(callback=callback, state=state)

    assert callback.message.reply_markup_edits == [None]
    assert patched_save_feedback == [(20, "нормальный отзыв", True)]
    assert state.cleared is True
    assert callback.message.answers == [UIConfig.FEEDBACK_THANKS_TEXT]
    assert _reply_button_texts(callback.message.answered_markups[0]) == _reply_button_texts(get_main_keyboard())
    assert callback.answered == [(None, False)]


async def test_handle_feedback_finish_saves_without_feedback_text(
    make_callback,
    fake_state,
    patched_save_feedback,
) -> None:
    """Завершение в ожидании отзыва сохраняет оценку без текста."""
    state = fake_state()
    await state.set_state(FeedbackStates.waiting_feedback)
    await state.update_data(feedback_evaluation=False, feedback_text=None)
    callback = make_callback(uid=21)

    await feedback_handlers.handle_feedback_finish(callback=callback, state=state)

    assert patched_save_feedback == [(21, None, False)]
    assert state.cleared is True


async def test_handle_feedback_finish_choice_treats_non_bool_evaluation_as_true(
    make_callback,
    fake_state,
    patched_save_feedback,
    monkeypatch,
) -> None:
    """Любое непустое значение оценки приводится к True."""
    monkeypatch.setattr(settings, "MIN_FEEDBACK_TEXT", 5)
    state = fake_state()
    await state.set_state(FeedbackStates.waiting_for_feedback_choice)
    await state.update_data(feedback_evaluation=1, feedback_text="длинный отзыв")
    callback = make_callback(uid=22)

    await feedback_handlers.handle_feedback_finish_choice(callback=callback, state=state)

    assert patched_save_feedback == [(22, "длинный отзыв", True)]
