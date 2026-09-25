"""Интеграционные тесты хендлеров обогатителя.

Реальная in-memory SQLite (см. conftest.py); внешний API обогатителя
мокается. Проверяется связка хендлеров с БД: авторизация и создание
ожидающей генерации с исходным и обогащённым промптами.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from core.database.models import User
from domains.enricher import handlers as enricher_handlers
from domains.enricher import service as enricher
from domains.enricher.fsm import PromptEnricherStates
from domains.generation.fsm import GenerationStates
from domains.generation.models import Generation


@pytest.fixture
def patched_enricher_db(db_sessionmaker, monkeypatch):
    """Перенаправляет обращение сервиса обогатителя к тестовой БД."""
    monkeypatch.setattr(enricher, "SessionLocal", db_sessionmaker)
    return db_sessionmaker


@pytest.fixture
def patched_enrich_api(monkeypatch):
    """Мокает вызов API обогатителя на уровне хендлеров, копя вызовы."""
    calls = []

    async def fake_enrich(prompt, history=None):
        calls.append({"prompt": prompt, "history": history})
        return f"обогащённый: {prompt}"

    monkeypatch.setattr(enricher_handlers, "enrich_prompt", fake_enrich)
    return calls


@pytest.fixture
def make_callback_message(make_message):
    """Сообщение-заглушка для callback: с edit_text и edit_reply_markup."""

    class FakeCallbackMessage(make_message):
        def __init__(self, text=None, uid=1):
            super().__init__(text=text, uid=uid)
            self.edits = []
            self.reply_markup_removed = False

        async def edit_text(self, new_text, **kwargs):
            self.edits.append(new_text)
            return self

        async def edit_reply_markup(self, reply_markup=None):
            self.reply_markup_removed = True

    return FakeCallbackMessage


@pytest.fixture
def make_callback(make_callback_message):
    """Фабрика callback-запросов-заглушек для inline-кнопок обогатителя."""

    class FakeCallback:
        def __init__(self, uid, message=None):
            self.from_user = SimpleNamespace(id=uid)
            self.message = message if message is not None else make_callback_message()
            self.answered = []

        async def answer(self, text=None, show_alert=False):
            self.answered.append((text, show_alert))

    return FakeCallback


async def _make_authorized_user(sessionmaker, tg_id: int) -> None:
    """Создаёт авторизованного пользователя в тестовой БД."""
    async with sessionmaker() as session:
        session.add(User(tg_id=tg_id, is_authorized=True))
        await session.commit()


async def test_full_enrichment_flow_saves_generation(
    patched_auth_db,
    patched_enricher_db,
    patched_enrich_api,
    clean_auth_state,
    make_message,
    fake_state,
    make_callback,
    make_callback_message,
):
    """Подтверждение обогащения сохраняет ожидающую генерацию."""
    await _make_authorized_user(patched_auth_db, tg_id=7)
    state = fake_state()
    await state.set_state(PromptEnricherStates.waiting_for_idea)

    message = make_message(text="грустная песня о дожде", uid=7)
    await enricher_handlers.handle_idea(message=message, state=state)

    assert state.state == PromptEnricherStates.waiting_for_approval
    assert (await state.get_data())["enriched_prompt"] == "обогащённый: грустная песня о дожде"

    callback = make_callback(uid=7, message=make_callback_message())
    await enricher_handlers.handle_prompt_approve(callback=callback, state=state)

    assert state.state == GenerationStates.waiting_for_title
    data = await state.get_data()
    assert data["prompt"] == enricher_handlers._build_generation_prompt(text="обогащённый: грустная песня о дожде")

    async with patched_enricher_db() as session:
        record = await session.scalar(select(Generation).where(Generation.user_id == 7))

    assert record is not None
    assert record.prompt == "грустная песня о дожде"
    assert record.enriched_prompt == {"text": "обогащённый: грустная песня о дожде"}
    assert record.title is None


async def test_approve_denied_for_unauthorized_user(
    patched_auth_db,
    patched_enricher_db,
    clean_auth_state,
    fake_state,
    make_callback,
    make_callback_message,
):
    """Неавторизованный пользователь не создаёт запись генерации."""
    state = fake_state()
    await state.update_data(prompt="идея", enriched_prompt="обогащённый")
    callback = make_callback(uid=99, message=make_callback_message())

    await enricher_handlers.handle_prompt_approve(callback=callback, state=state)

    assert callback.answered == [("Доступ закрыт. Авторизуйтесь заново: /start", True)]
    assert state.cleared

    async with patched_enricher_db() as session:
        record = await session.scalar(select(Generation).where(Generation.user_id == 99))

    assert record is None


async def test_fallback_saves_generation_with_raw_prompt(
    patched_auth_db,
    patched_enricher_db,
    clean_auth_state,
    fake_state,
    make_callback,
    make_callback_message,
):
    """Fallback сохраняет исходный промпт как обогащённый, если обогатитель недоступен."""
    await _make_authorized_user(patched_auth_db, tg_id=5)
    state = fake_state()
    await state.update_data(prompt="идея без обогащения")
    callback = make_callback(uid=5, message=make_callback_message())

    await enricher_handlers.handle_prompt_fallback(callback=callback, state=state)

    assert state.state == GenerationStates.waiting_for_title
    async with patched_enricher_db() as session:
        record = await session.scalar(select(Generation).where(Generation.user_id == 5))

    assert record is not None
    assert record.prompt == "идея без обогащения"
    assert record.enriched_prompt == {"text": "идея без обогащения"}
