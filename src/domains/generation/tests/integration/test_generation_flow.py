"""Интеграционные тесты процесса генерации: FSM, прогресс, сбой, отмена.

Сервис генерации замокан на уровне domains.generation.service.run_generation
(место использования — патчим там, где вызывается), поэтому тесты идут
через реальные хендлеры, но без сети.
"""

import asyncio
from types import SimpleNamespace

import pytest

from core.config import settings
from core.database import User
from domains.base import handlers as base_handlers
from domains.enricher import handlers as handlers_enricher
from domains.enricher.fsm import PromptEnricherStates
from domains.evaluation.fsm import FeedbackStates
from domains.generation import handlers as handlers_generation
from domains.generation import pipeline_handlers as pipeline
from domains.generation import service as service_generation
from domains.generation.registries import task_registry
from domains.generation.registries.task_registry import _active_tasks as registry

pytestmark = pytest.mark.integration


@pytest.fixture
def clean_generation_registry(fake_redis, monkeypatch):
    """Пустой реестр активных задач генерации до и после теста.

    task_registry теперь тоже ходит в Redis — подменяем его клиент
    на тот же fakeredis, что и в auth_registry.
    """
    monkeypatch.setattr(task_registry, "redis_client", fake_redis)
    registry.clear()
    yield
    registry.clear()


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


def _install_generation(monkeypatch, impl):
    monkeypatch.setattr(service_generation, "run_generation", impl)


async def test_cmd_generate_sends_hint_and_sets_state(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Кнопка «🎵 Сгенерировать» открывает диалог генерации.

    Проверяем, что авторизованному пользователю уходят оба сообщения —
    расширенная подсказка и копируемый шаблон с маркерами «Жанр:» —
    и что FSM переключается в waiting_for_idea (дальше диалог ведёт
    сценарий обогащения).
    """
    await _make_authorized_user(patched_auth_db, 50)
    msg = make_message(uid=50)
    state = fake_state()

    await handlers_generation.cmd_generate(msg, state)

    assert any("Опишите песню" in answer for answer in msg.answers)
    assert any("Жанр:" in answer for answer in msg.answers)
    assert state.state is PromptEnricherStates.waiting_for_idea


async def test_cmd_generate_blocked_while_generating(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """«🎵 Сгенерировать» заблокирована во время идущей генерации.

    При выставленном флаге generating хендлер отвечает просьбой
    подождать и не переключает FSM-состояние.
    """
    await _make_authorized_user(patched_auth_db, 51)
    msg = make_message(uid=51)
    state = fake_state()
    await state.update_data(generating=True)

    await handlers_generation.cmd_generate(msg, state)

    assert "Дождитесь окончания" in msg.answers[-1]
    assert state.state is None


async def test_cmd_generate_requires_auth(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Неавторизованный пользователь не попадает в диалог генерации.

    _require_auth отсекает запрос до проверки флага generating:
    подсказка и шаблон не отправляются, FSM-состояние не переключается.
    """
    msg = make_message(uid=70)
    state = fake_state()

    await handlers_generation.cmd_generate(msg, state)

    assert not any("Опишите песню" in answer for answer in msg.answers)
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

    assert "Слишком длинный" in msg.answers[-1]
    assert state.state is None


async def test_handle_idea_rejects_blank_text(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Описание из одних пробелов отклоняется."""
    msg = make_message(text="   ", uid=71)
    state = fake_state()

    await handlers_enricher.handle_idea(msg, state)

    assert "непустой текст" in msg.answers[-1]
    assert state.state is None


async def test_handle_idea_rejects_untouched_template(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Нетронутый шаблон отклоняется."""
    template = "Жанр: \n\nНастроение: \n\nИнструменты: \n\nТемп и ритм: \n\nГолос: \n\nТекст песни: \n"
    msg = make_message(text=template, uid=72)
    state = fake_state()

    await handlers_enricher.handle_idea(msg, state)

    assert "Шаблон пришёл пустым" in msg.answers[-1]
    assert state.state is None


async def test_handle_title_runs_generation_to_completion(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Успешная генерация от названия до отправки аудио и перехода в оценку."""
    await _make_authorized_user(patched_auth_db, 55)
    monkeypatch.setattr(pipeline, "get_session", patched_auth_db)

    async def fake_generate(prompt, on_progress=None):
        if on_progress is not None:
            await on_progress("Получаю аудио…", 0.5)
        return b"audio-bytes"

    _install_generation(monkeypatch, fake_generate)

    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Моя песня", uid=55)

    await handlers_generation.handle_title(msg, state)

    assert any("50%" in edit for edit in msg.sent[0].edits)
    assert len(msg.audios) == 1
    assert msg.audios[0].filename == "Моя_песня.mp3"
    assert state.state is FeedbackStates.waiting_evaluation
    assert 55 not in registry


async def test_generate_failure_leaves_retry_button(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Сбой генерации сохраняет prompt и title для бесплатного повтора."""
    await _make_authorized_user(patched_auth_db, 56)

    async def failing_generate(prompt, on_progress=None):
        raise RuntimeError("server exploded")

    _install_generation(monkeypatch, failing_generate)

    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Название", uid=56)

    await handlers_generation.handle_title(msg, state)

    assert "Не получилось сгенерировать" in msg.sent[-1].text
    data = await state.get_data()
    assert data["prompt"] == "промпт"
    assert data["title"] == "Название"
    assert data["generating"] is False


async def test_cancel_generation_kills_running_task(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Кнопка «❌ Отмена» гасит живую фоновую задачу генерации."""
    msg = make_message(uid=57)
    state = fake_state()
    task = asyncio.create_task(asyncio.sleep(60))
    registry[57] = task

    await base_handlers.cmd_cancel(msg, state)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.cleared
    assert "Действие отменено" in msg.answers[-1]


async def test_retry_generation_requires_auth(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, make_callback
):
    """Кнопка повтора не работает для неавторизованных."""
    state = fake_state()
    await state.update_data(prompt="промпт", title="название")
    msg = make_message(uid=58)
    callback = make_callback(58, msg)

    await handlers_generation.retry_generation(callback, state)

    assert "Доступ закрыт" in callback.answered[0][0]
    assert state.cleared
    assert len(msg.audios) == 0


async def test_retry_generation_without_prompt_suggests_restart(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, make_callback
):
    """Ретрай без сохранённого промпта предлагает начать заново."""
    await _make_authorized_user(patched_auth_db, 59)
    state = fake_state()
    callback = make_callback(59, make_message(uid=59))

    await handlers_generation.retry_generation(callback, state)

    assert "Начните заново" in callback.answered[0][0]
    assert state.cleared


async def test_generate_and_send_blocked_inside_lock(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Проверка внутри лока блокирует второй запуск генерации."""
    await _make_authorized_user(patched_auth_db, 60)

    async def unexpected_generate(prompt, on_progress=None):
        raise AssertionError("generate не должен вызываться при активной генерации")

    _install_generation(monkeypatch, unexpected_generate)

    state = fake_state()
    await state.update_data(generating=True, prompt="промпт")
    msg = make_message(uid=60)

    await pipeline.generate_and_send(msg, state, "промпт", "Название", 60)

    assert "Дождитесь окончания" in msg.answers[-1]
    assert len(msg.audios) == 0


async def test_mock_mode_generates_audio(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Ветка MOCK_MODE генерирует демо-прогресс и отправляет аудио из load_mock_audio."""
    monkeypatch.setattr(settings.generation, "MOCK_MODE", True)
    monkeypatch.setattr(service_generation, "PROGRESS_EDIT_INTERVAL", 0.01)
    monkeypatch.setattr(service_generation, "load_mock_audio", lambda: b"mock-audio")
    monkeypatch.setattr(pipeline, "get_session", patched_auth_db)

    await _make_authorized_user(patched_auth_db, 61)
    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Демо", uid=61)

    await handlers_generation.handle_title(msg, state)

    assert any("демо-режим" in edit for edit in msg.sent[0].edits)
    assert msg.audios[0].filename == "Демо.mp3"
    assert state.state is FeedbackStates.waiting_evaluation


async def test_cancelled_generation_deletes_status_and_unsets_flag(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Отмена генерации удаляет сообщение прогресса и снимает флаг generating."""
    await _make_authorized_user(patched_auth_db, 62)

    async def hanging_generate(prompt, on_progress=None):
        await asyncio.sleep(60)

    monkeypatch.setattr(service_generation, "run_generation", hanging_generate)

    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Отмена", uid=62)

    task = asyncio.create_task(handlers_generation.handle_title(msg, state))
    for _ in range(100):
        if 62 in registry and not registry[62].done():
            break
        await asyncio.sleep(0.01)

    registry[62].cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert msg.sent[0].deleted
    data = await state.get_data()
    assert data["generating"] is False
    assert 62 not in registry


async def test_generation_failure_notifies_owner(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Сбой генерации уведомляет владельца."""
    await _make_authorized_user(patched_auth_db, 63)

    async def failing_generate(prompt, on_progress=None):
        raise RuntimeError("boom")

    _install_generation(monkeypatch, failing_generate)

    notified = []

    async def fake_notify_owner(bot, context, err):
        notified.append((context, err))

    monkeypatch.setattr(pipeline, "notify_owner", fake_notify_owner)

    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Сбой", uid=63)

    await handlers_generation.handle_title(msg, state)

    assert len(notified) == 1
    assert "user=63" in notified[0][0]
    assert isinstance(notified[0][1], RuntimeError)


async def test_handle_title_rejects_empty_title(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Название из одних пробелов отклоняется."""
    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="   ", uid=64)

    await handlers_generation.handle_title(msg, state)

    assert "непустое название" in msg.answers[-1]
    assert state.state is None


async def test_handle_title_rejects_too_long_title(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Слишком длинное название отклоняется."""
    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="а" * (handlers_generation.MAX_TITLE_LEN + 1), uid=65)

    await handlers_generation.handle_title(msg, state)

    assert "Слишком длинное название" in msg.answers[-1]
    assert state.state is None


async def test_handle_title_blocked_while_generating(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """handle_title не запускает генерацию при активной генерации."""
    state = fake_state()
    await state.update_data(prompt="промпт", generating=True)
    msg = make_message(text="Название", uid=66)

    await handlers_generation.handle_title(msg, state)

    assert "Дождитесь окончания" in msg.answers[-1]
    assert "title" not in (await state.get_data())


async def test_handle_title_missing_prompt_suggests_restart(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Генерация без сохранённого промпта предлагает начать заново."""
    state = fake_state()
    msg = make_message(text="Название", uid=73)

    await handlers_generation.handle_title(msg, state)

    assert "Описание песни потерялось" in msg.answers[-1]
    assert state.cleared
    assert len(msg.audios) == 0


async def test_retry_generation_blocked_while_generating(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, make_callback
):
    """Ретрай заблокирован во время идущей генерации."""
    await _make_authorized_user(patched_auth_db, 67)
    state = fake_state()
    await state.update_data(prompt="промпт", title="название", generating=True)
    callback = make_callback(67, make_message(uid=67))

    await handlers_generation.retry_generation(callback, state)

    assert callback.answered == [("Генерация уже идёт.", True)]
    assert len(callback.message.audios) == 0


async def test_retry_generation_runs_generation(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, make_callback, monkeypatch
):
    """Успешный ретрай после сбоя."""
    await _make_authorized_user(patched_auth_db, 68)
    monkeypatch.setattr(pipeline, "get_session", patched_auth_db)

    async def fake_generate(prompt, on_progress=None):
        return b"retry-audio"

    _install_generation(monkeypatch, fake_generate)

    state = fake_state()
    await state.update_data(prompt="промпт", title="Ретрай")
    msg = make_message(uid=68)
    callback = make_callback(68, msg)

    await handlers_generation.retry_generation(callback, state)

    assert msg.deleted
    assert callback.answered[-1] == (None, False)
    assert len(msg.audios) == 1
    assert msg.audios[0].filename == "Ретрай.mp3"
    assert state.state is FeedbackStates.waiting_evaluation
