"""Юнит-тесты хендлеров обогатителя (handlers/enricher_handlers.py).

Внешние зависимости мокаются: вызов API обогатителя, авторизация,
сохранение фидбека в БД и уведомление владельца. Тестируется логика
FSM-диалога: валидации, ветвления и переходы состояний.
"""

from types import SimpleNamespace

import pytest

from fsm.enricher_fsm import PromptEnricherStates
from fsm.generation_fsm import MAX_PROMPT_LEN, GenerationStates
from handlers import enricher_handlers


@pytest.fixture
def auth_stub(monkeypatch):
    """Подменяет is_authorized в хендлерах на переключаемую заглушку."""

    class AuthStub:
        def __init__(self):
            self.authorized = True
            self.calls = []

        async def __call__(self, uid: int) -> bool:
            self.calls.append(uid)
            return self.authorized

    stub = AuthStub()
    monkeypatch.setattr(enricher_handlers, "is_authorized", stub)
    return stub


@pytest.fixture
def enrich_stub(monkeypatch):
    """Подменяет enrich_prompt в хендлерах на заглушку с настраиваемым результатом."""

    class EnrichStub:
        def __init__(self):
            self.result = "обогащённый промпт"
            self.error = None
            self.calls = []

        async def __call__(self, prompt, history=None):
            self.calls.append({"prompt": prompt, "history": history})
            if self.error is not None:
                raise self.error
            return self.result

    stub = EnrichStub()
    monkeypatch.setattr(enricher_handlers, "enrich_prompt", stub)
    return stub


@pytest.fixture
def save_stub(monkeypatch):
    """Подменяет save_enriched_prompt в хендлерах, копя вызовы."""
    calls = []

    async def fake_save(tg_id, initial_prompt, enriched_prompt):
        calls.append({"tg_id": tg_id, "initial_prompt": initial_prompt, "enriched_prompt": enriched_prompt})

    monkeypatch.setattr(enricher_handlers, "save_enriched_prompt", fake_save)
    return calls


@pytest.fixture
def notify_stub(monkeypatch):
    """Подменяет notify_owner в хендлерах на заглушку, копящую вызовы."""

    class NotifyStub:
        def __init__(self):
            self.calls = []

        async def __call__(self, bot=None, context=None, err=None):
            self.calls.append({"bot": bot, "context": context, "err": err})

    stub = NotifyStub()
    monkeypatch.setattr(enricher_handlers, "notify_owner", stub)
    return stub


@pytest.fixture
def make_callback_message(make_message):
    """Сообщение-заглушка для callback: с edit_text, edit_reply_markup и историей правок."""

    class FakeCallbackMessage(make_message):
        def __init__(self, text=None, uid=1):
            super().__init__(text=text, uid=uid)
            self.edits = []
            self.reply_markup_removed = False

        async def edit_text(self, text, **kwargs):
            self.edits.append(text)
            return self

        async def edit_reply_markup(self, reply_markup=None):
            self.reply_markup_removed = True

    return FakeCallbackMessage


@pytest.fixture
def make_callback(make_callback_message):
    """Фабрика callback-запросов-заглушек для инлайн-кнопок обогатителя."""

    class FakeCallback:
        def __init__(self, uid, message=None):
            self.from_user = SimpleNamespace(id=uid)
            self.message = message if message is not None else make_callback_message()
            self.answered = []

        async def answer(self, text=None, show_alert=False):
            self.answered.append((text, show_alert))

    return FakeCallback


# --- _build_generation_prompt ---


def test_build_generation_prompt_structured_brief():
    text = "Жанр: рок\n\nТекст песни: слова песни"

    result = enricher_handlers._build_generation_prompt(text=text)

    assert "brief" in result
    assert text in result


def test_build_generation_prompt_plain_lyrics():
    result = enricher_handlers._build_generation_prompt(text="просто стихи о море")

    assert "lyrics" in result
    assert "просто стихи о море" in result


# --- handle_idea ---


async def test_handle_idea_success(make_message, fake_state, enrich_stub):
    message = make_message(text="грустная песня о дожде")
    state = fake_state()

    await enricher_handlers.handle_idea(message=message, state=state)

    data = await state.get_data()
    assert data["prompt"] == "грустная песня о дожде"
    assert data["enriching"] is False
    assert data["enriched_prompt"] == "обогащённый промпт"
    assert enrich_stub.calls[0]["prompt"] == "грустная песня о дожде"
    assert enrich_stub.calls[0]["history"] is None
    assert state.state == PromptEnricherStates.waiting_for_approval
    assert len(message.sent) == 1
    assert "Я подготовил описание песни" in message.sent[0].edits[0]


async def test_handle_idea_empty_text(make_message, fake_state, enrich_stub):
    message = make_message(text="   ")
    state = fake_state()

    await enricher_handlers.handle_idea(message=message, state=state)

    assert "непустой текст" in message.answers[0]
    assert enrich_stub.calls == []


async def test_handle_idea_too_long(make_message, fake_state, enrich_stub):
    message = make_message(text="а" * (MAX_PROMPT_LEN + 1))
    state = fake_state()

    await enricher_handlers.handle_idea(message=message, state=state)

    assert "Слишком длинный текст" in message.answers[0]
    assert enrich_stub.calls == []


async def test_handle_idea_empty_template(make_message, fake_state, enrich_stub):
    message = make_message(text=enricher_handlers.PROMPT_TEMPLATE)
    state = fake_state()

    await enricher_handlers.handle_idea(message=message, state=state)

    assert "Шаблон пришёл пустым" in message.answers[0]
    assert enrich_stub.calls == []


async def test_handle_idea_while_enriching(make_message, fake_state, enrich_stub):
    message = make_message(text="идея")
    state = fake_state()
    await state.update_data(enriching=True)

    await enricher_handlers.handle_idea(message=message, state=state)

    assert "Дождитесь окончания обогащения" in message.answers[0]
    assert enrich_stub.calls == []


async def test_handle_idea_enrich_failed_shows_retry(make_message, fake_state, enrich_stub):
    enrich_stub.result = None
    message = make_message(text="идея")
    state = fake_state()

    await enricher_handlers.handle_idea(message=message, state=state)

    assert "Не получилось обогатить" in message.sent[0].edits[0]
    assert state.state is None


async def test_handle_idea_not_configured_notifies_owner(make_message, fake_state, enrich_stub, notify_stub):
    enrich_stub.error = ValueError("Enricher URL or model is not configured")
    message = make_message(text="идея")
    state = fake_state()

    await enricher_handlers.handle_idea(message=message, state=state)

    assert len(notify_stub.calls) == 1
    assert "не сконфигурирован" in notify_stub.calls[0]["context"]
    assert "Сервис обогащения не настроен" in message.sent[0].edits[0]
    assert (await state.get_data())["enriching"] is False


async def test_handle_idea_drops_stale_result(make_message, fake_state, monkeypatch):
    message = make_message(text="идея")
    state = fake_state()

    async def fake_enrich(prompt, history=None):
        # Сценарий отменили, пока обогащение работало
        await state.clear()
        return "обогащённый промпт"

    monkeypatch.setattr(enricher_handlers, "enrich_prompt", fake_enrich)

    await enricher_handlers.handle_idea(message=message, state=state)

    assert len(message.sent[0].edits) == 0
    assert state.cleared


# --- handle_prompt_approve ---


async def test_approve_unauthorized(make_callback, fake_state, auth_stub, save_stub):
    auth_stub.authorized = False
    state = fake_state()
    await state.update_data(prompt="идея", enriched_prompt="обогащённый")
    callback = make_callback(uid=7)

    await enricher_handlers.handle_prompt_approve(callback=callback, state=state)

    assert callback.answered == [("Доступ закрыт. Авторизуйтесь заново: /start", True)]
    assert state.cleared


async def test_approve_without_enriched_prompts_restart(make_callback, fake_state, auth_stub):
    state = fake_state()
    await state.update_data(prompt="идея")
    callback = make_callback(uid=7)

    await enricher_handlers.handle_prompt_approve(callback=callback, state=state)

    assert callback.answered[0][0] == "Начните заново: 🎵 Сгенерировать"
    assert state.cleared


async def test_approve_finalizes_prompt_and_saves_feedback(
    make_callback, make_callback_message, fake_state, auth_stub, save_stub
):
    state = fake_state()
    await state.update_data(prompt="идея", enriched_prompt="обогащённый промпт")
    callback = make_callback(uid=7, message=make_callback_message())

    await enricher_handlers.handle_prompt_approve(callback=callback, state=state)

    data = await state.get_data()
    assert data["prompt"] == enricher_handlers._build_generation_prompt(text="обогащённый промпт")
    assert state.state == GenerationStates.waiting_for_title
    assert save_stub == [{"tg_id": 7, "initial_prompt": "идея", "enriched_prompt": "обогащённый промпт"}]
    assert callback.message.reply_markup_removed
    assert "Введите название песни" in callback.message.answers[0]
    assert callback.answered == [(None, False)]


# --- handle_prompt_edit ---


async def test_edit_unauthorized(make_callback, fake_state, auth_stub):
    auth_stub.authorized = False
    state = fake_state()
    callback = make_callback(uid=7)

    await enricher_handlers.handle_prompt_edit(callback=callback, state=state)

    assert callback.answered[0][1] is True
    assert state.cleared


async def test_edit_sets_waiting_for_edits(make_callback, make_callback_message, fake_state, auth_stub):
    state = fake_state()
    callback = make_callback(uid=7, message=make_callback_message())

    await enricher_handlers.handle_prompt_edit(callback=callback, state=state)

    assert state.state == PromptEnricherStates.waiting_for_edits
    assert callback.message.reply_markup_removed
    assert "правки" in callback.message.answers[0]


# --- handle_prompt_edits ---


async def test_prompt_edits_success_sends_history(make_message, fake_state, enrich_stub):
    message = make_message(text="сделай веселее")
    state = fake_state()
    await state.update_data(prompt="идея", enriched_prompt="прошлый вариант")

    await enricher_handlers.handle_prompt_edits(message=message, state=state)

    assert enrich_stub.calls[0] == {
        "prompt": "сделай веселее",
        "history": [
            {"role": "user", "content": "идея"},
            {"role": "assistant", "content": "прошлый вариант"},
        ],
    }
    assert (await state.get_data())["pending_edits"] == "сделай веселее"
    assert state.state == PromptEnricherStates.waiting_for_approval


async def test_prompt_edits_empty(make_message, fake_state, enrich_stub):
    message = make_message(text="   ")
    state = fake_state()
    await state.update_data(prompt="идея")

    await enricher_handlers.handle_prompt_edits(message=message, state=state)

    assert "непустые правки" in message.answers[0]
    assert enrich_stub.calls == []


async def test_prompt_edits_too_long(make_message, fake_state, enrich_stub):
    message = make_message(text="а" * (MAX_PROMPT_LEN + 1))
    state = fake_state()
    await state.update_data(prompt="идея")

    await enricher_handlers.handle_prompt_edits(message=message, state=state)

    assert "Слишком длинный текст" in message.answers[0]
    assert enrich_stub.calls == []


async def test_prompt_edits_lost_session(make_message, fake_state, enrich_stub):
    message = make_message(text="правки")
    state = fake_state()

    await enricher_handlers.handle_prompt_edits(message=message, state=state)

    assert "потерялась" in message.answers[0]
    assert state.cleared
    assert enrich_stub.calls == []


async def test_prompt_edits_while_enriching(make_message, fake_state, enrich_stub):
    message = make_message(text="правки")
    state = fake_state()
    await state.update_data(prompt="идея", enriching=True)

    await enricher_handlers.handle_prompt_edits(message=message, state=state)

    assert "Дождитесь окончания обогащения" in message.answers[0]
    assert enrich_stub.calls == []


# --- handle_prompt_retry ---


async def test_retry_unauthorized(make_callback, fake_state, auth_stub):
    auth_stub.authorized = False
    state = fake_state()
    callback = make_callback(uid=7)

    await enricher_handlers.handle_prompt_retry(callback=callback, state=state)

    assert callback.answered[0][1] is True
    assert state.cleared


async def test_retry_while_enriching(make_callback, fake_state, auth_stub, enrich_stub):
    state = fake_state()
    await state.update_data(prompt="идея", enriching=True)
    callback = make_callback(uid=7)

    await enricher_handlers.handle_prompt_retry(callback=callback, state=state)

    assert callback.answered == [("Обогащение уже выполняется.", True)]
    assert enrich_stub.calls == []


async def test_retry_without_prompt_prompts_restart(make_callback, fake_state, auth_stub):
    state = fake_state()
    callback = make_callback(uid=7)

    await enricher_handlers.handle_prompt_retry(callback=callback, state=state)

    assert callback.answered[0][0] == "Начните заново: 🎵 Сгенерировать"
    assert state.cleared


async def test_retry_first_run_enriches_idea(make_callback, make_callback_message, fake_state, auth_stub, enrich_stub):
    state = fake_state()
    await state.update_data(prompt="идея")
    callback = make_callback(uid=7, message=make_callback_message())

    await enricher_handlers.handle_prompt_retry(callback=callback, state=state)

    assert enrich_stub.calls[0] == {"prompt": "идея", "history": None}
    assert state.state == PromptEnricherStates.waiting_for_approval
    assert "Я подготовил описание песни" in callback.message.edits[-1]


async def test_retry_after_edits_sends_history(
    make_callback, make_callback_message, fake_state, auth_stub, enrich_stub
):
    state = fake_state()
    await state.update_data(prompt="идея", enriched_prompt="прошлый вариант", pending_edits="правки")
    callback = make_callback(uid=7, message=make_callback_message())

    await enricher_handlers.handle_prompt_retry(callback=callback, state=state)

    assert enrich_stub.calls[0]["prompt"] == "правки"
    assert enrich_stub.calls[0]["history"] == [
        {"role": "user", "content": "идея"},
        {"role": "assistant", "content": "прошлый вариант"},
    ]


# --- handle_prompt_fallback ---


async def test_fallback_unauthorized(make_callback, fake_state, auth_stub):
    auth_stub.authorized = False
    state = fake_state()
    callback = make_callback(uid=7)

    await enricher_handlers.handle_prompt_fallback(callback=callback, state=state)

    assert callback.answered[0][1] is True
    assert state.cleared


async def test_fallback_without_prompt_prompts_restart(make_callback, fake_state, auth_stub):
    state = fake_state()
    callback = make_callback(uid=7)

    await enricher_handlers.handle_prompt_fallback(callback=callback, state=state)

    assert callback.answered[0][0] == "Начните заново: 🎵 Сгенерировать"
    assert state.cleared


async def test_fallback_uses_enriched_prompt(make_callback, make_callback_message, fake_state, auth_stub, save_stub):
    state = fake_state()
    await state.update_data(prompt="идея", enriched_prompt="прошлый обогащённый")
    callback = make_callback(uid=7, message=make_callback_message())

    await enricher_handlers.handle_prompt_fallback(callback=callback, state=state)

    data = await state.get_data()
    assert data["prompt"] == enricher_handlers._build_generation_prompt(text="прошлый обогащённый")
    assert state.state == GenerationStates.waiting_for_title
    assert save_stub[0]["enriched_prompt"] == "прошлый обогащённый"
    assert "Введите название песни" in callback.message.answers[0]


async def test_fallback_uses_raw_prompt_without_enriched(
    make_callback, make_callback_message, fake_state, auth_stub, save_stub
):
    state = fake_state()
    await state.update_data(prompt="идея")
    callback = make_callback(uid=7, message=make_callback_message())

    await enricher_handlers.handle_prompt_fallback(callback=callback, state=state)

    data = await state.get_data()
    assert data["prompt"] == enricher_handlers._build_generation_prompt(text="идея")
    assert save_stub[0]["enriched_prompt"] == "идея"


# --- handle_prompt_cancel ---


async def test_cancel_clears_state_and_returns_to_menu(make_callback, make_callback_message, fake_state, auth_stub):
    state = fake_state()
    await state.update_data(prompt="идея")
    callback = make_callback(uid=7, message=make_callback_message())

    await enricher_handlers.handle_prompt_cancel(callback=callback, state=state)

    assert state.cleared
    assert "отменён" in callback.message.edits[0]
    assert "главное меню" in callback.message.answers[0]
    assert callback.answered == [(None, False)]
