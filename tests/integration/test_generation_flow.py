"""Интеграционные тесты процесса генерации: FSM, прогресс, сбой, отмена.

Сервис генерации замокан на уровне services.pipeline.generate_song_real
(место использования — патчим там, где вызывается), поэтому тесты идут
через реальные хендлеры, но без сети.
"""

import asyncio
from types import SimpleNamespace

import pytest

from core.config import settings
from core.database import User
from fsm.registries.task_registry import active_tasks as registry
from handlers import base_handlers
from handlers import generation_handlers as handlers_generation
from handlers import generation_pipeline as pipeline
from handlers.generation_handlers import GenerationStates
from services import pipeline as service_pipeline

pytestmark = pytest.mark.integration


@pytest.fixture
def clean_generation_registry():
    """Пустой реестр активных задач генерации до и после теста."""
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
    monkeypatch.setattr(service_pipeline, "generate_song_real", impl)


async def test_cmd_generate_sends_hint_and_sets_state(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Команда «🎵 Сгенерировать» открывает диалог генерации.

    Проверяем, что авторизованному пользователю уходят оба сообщения —
    расширенная подсказка и копируемый шаблон с маркерами «Жанр:» —
    и что FSM переключается в waiting_for_prompt.
    """
    await _make_authorized_user(patched_auth_db, 50)
    msg = make_message(uid=50)
    state = fake_state()

    await handlers_generation.cmd_generate(msg, state)

    assert any("Опишите песню" in a for a in msg.answers)
    assert any("Жанр:" in a for a in msg.answers)  # копируемый шаблон
    assert state.state is GenerationStates.waiting_for_prompt


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
    assert state.state is None  # состояние не переключилось


async def test_cmd_generate_requires_auth(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Неавторизованный пользователь не попадает в диалог генерации (строка 82).

    _require_auth отсекает запрос до проверки флага generating:
    подсказка и шаблон не отправляются, FSM-состояние не переключается.
    """
    # Пользователя 70 в БД нет — доступ не выдан
    msg = make_message(uid=70)
    state = fake_state()

    await handlers_generation.cmd_generate(msg, state)

    assert not any("Опишите песню" in a for a in msg.answers)
    assert state.state is None


async def test_handle_prompt_with_template_wraps_in_brief(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Заполненный шаблон оборачивается в бриф для модели.

    Если в тексте есть маркеры вида «Жанр:», промпт сохраняется в FSM
    с обёрткой «brief» и явным указанием петь по-русски; состояние
    переключается на ожидание названия.
    """
    msg = make_message(text="Жанр: рок\nТекст песни: раз-два", uid=52)
    state = fake_state()

    await handlers_generation.handle_prompt(msg, state)

    data = await state.get_data()
    assert "brief" in data["prompt"]
    assert state.state is GenerationStates.waiting_for_title
    assert "название песни" in msg.answers[-1]


async def test_handle_prompt_plain_lyrics(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Простые стихи без маркеров шаблона идут с формулировкой «these lyrics».

    Мягкая обёртка не пугает модель словом «brief», когда пользователь
    прислал только текст песни.
    """
    msg = make_message(text="Просто стихи про кота", uid=53)
    state = fake_state()

    await handlers_generation.handle_prompt(msg, state)

    data = await state.get_data()
    assert "these lyrics" in data["prompt"]


async def test_handle_prompt_rejects_too_long(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Слишком длинное описание песни отклоняется.

    Текст длиннее MAX_PROMPT_LEN не сохраняется в FSM, состояние
    остаётся прежним, пользователю сообщается лимит.
    """
    msg = make_message(text="а" * (handlers_generation.MAX_PROMPT_LEN + 1), uid=54)
    state = fake_state()

    await handlers_generation.handle_prompt(msg, state)

    assert "Слишком длинный" in msg.answers[-1]
    assert state.state is None


async def test_handle_prompt_rejects_blank_text(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Описание из одних пробелов отклоняется (строки 117–118).

    После strip остаётся пустая строка: пользователю предлагается
    ввести непустой текст, FSM-состояние не меняется.
    """
    msg = make_message(text="   ", uid=71)
    state = fake_state()

    await handlers_generation.handle_prompt(msg, state)

    assert "непустой текст" in msg.answers[-1]
    assert state.state is None


async def test_handle_prompt_rejects_untouched_template(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Нетронутый шаблон (все поля пустые) отклоняется (строки 126–127).

    Регулярка _EMPTY_FIELD_RE вычищает пустые поля шаблона — остаётся
    пустая строка, пользователю предлагается заполнить хотя бы
    поле «Текст песни», FSM-состояние не меняется.
    """
    template = "Жанр: \n\nНастроение: \n\nИнструменты: \n\nТемп и ритм: \n\nГолос: \n\nТекст песни: \n"
    msg = make_message(text=template, uid=72)
    state = fake_state()

    await handlers_generation.handle_prompt(msg, state)

    assert "Шаблон пришёл пустым" in msg.answers[-1]
    assert state.state is None


async def test_handle_title_runs_generation_to_completion(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Успешная генерация от названия до отправки аудио.

    Замоканный сервис вызывает on_progress — сообщение статуса
    редактируется с процентами; аудио уходит с санитизированным именем
    файла (пробелы → подчёркивания), FSM очищается, задача снимается
    с реестра active_tasks.
    """
    await _make_authorized_user(patched_auth_db, 55)

    async def fake_generate(prompt, on_progress=None):
        if on_progress is not None:
            await on_progress("Получаю аудио…", 0.5)
        return b"audio-bytes"

    _install_generation(monkeypatch, fake_generate)

    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Моя песня", uid=55)

    await handlers_generation.handle_title(msg, state)

    # Прогресс-бар отредактировал сообщение статуса
    assert any("50%" in edit for edit in msg.sent[0].edits)
    # Аудио отправлено, состояние сброшено, задача снята с реестра
    assert len(msg.audios) == 1
    # Пробелы в имени файла санитизируются в подчёркивания
    assert msg.audios[0].filename == "Моя_песня.mp3"
    assert state.cleared
    assert 55 not in registry


async def test_generate_failure_leaves_retry_button(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Сбой генерации сохраняет prompt и title для бесплатного повтора.

    При исключении в сервисе пользователю показывается сообщение с
    извинением, FSM НЕ очищается (prompt и title остаются), а флаг
    generating снимается, чтобы можно было запустить новую генерацию.
    """
    await _make_authorized_user(patched_auth_db, 56)

    async def failing_generate(prompt, on_progress=None):
        raise RuntimeError("server exploded")

    _install_generation(monkeypatch, failing_generate)

    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Название", uid=56)

    await handlers_generation.handle_title(msg, state)

    assert "Не получилось сгенерировать" in msg.sent[-1].text
    # prompt и title остались в FSM для повтора, флаг generating снят
    data = await state.get_data()
    assert data["prompt"] == "промпт"
    assert data["title"] == "Название"
    assert data["generating"] is False


async def test_cancel_generation_kills_running_task(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Кнопка «❌ Отмена» гасит живую фоновую задачу генерации.

    Задача из active_tasks отменяется (await завершается
    CancelledError), FSM очищается, пользователь получает подтверждение.
    """
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
    """Кнопка повтора не работает для неавторизованных.

    Кнопка могла остаться в чате после logout: пользователь без доступа
    получает alert с предложением авторизоваться заново, FSM очищается,
    генерация не запускается.
    """
    # Пользователя 58 в БД нет — доступ отозван/никогда не выдавался
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
    """Ретрай без сохранённого промпта предлагает начать заново.

    Если FSM-состояние потерялось (например, после перезапуска бота),
    пользователь получает alert «Начните заново», состояние очищается.
    """
    await _make_authorized_user(patched_auth_db, 59)
    state = fake_state()  # промпт потерялся (перезапуск бота)
    callback = make_callback(59, make_message(uid=59))

    await handlers_generation.retry_generation(callback, state)

    assert "Начните заново" in callback.answered[0][0]
    assert state.cleared


async def test_generate_and_send_blocked_inside_lock(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Guard внутри лока: если generating уже выставлен — второй запуск ничего не делает.

    Покрывает guard в generate_and_send: параллельный запуск,
    проскочивший внешнюю проверку в handle_title, атомарно отсекается
    под _generation_lock — пользователь получает «Дождитесь окончания»,
    сервис генерации не вызывается.
    """
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
    """Ветка MOCK_MODE (строка 113): демо-прогресс, аудио из load_mock_audio.

    При MOCK_MODE=True реальный сервис не вызывается (защищаемся
    AssertionError), вместо этого крутится демо-прогресс с текстом
    «демо-режим» и отправляется аудио из load_mock_audio.
    PROGRESS_EDIT_INTERVAL уменьшен, иначе тест спал бы ~9 секунд.
    """
    monkeypatch.setattr(settings.generation, "MOCK_MODE", True)
    # Ускоряем демо-прогресс, иначе тест спит ~9 секунд
    monkeypatch.setattr(service_pipeline, "PROGRESS_EDIT_INTERVAL", 0.01)
    monkeypatch.setattr(service_pipeline, "load_mock_audio", lambda: b"mock-audio")

    async def unexpected_generate(prompt, on_progress=None):
        raise AssertionError("в mock-режиме generate_song_real не вызывается")

    _install_generation(monkeypatch, unexpected_generate)

    await _make_authorized_user(patched_auth_db, 61)
    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Демо", uid=61)

    await handlers_generation.handle_title(msg, state)

    assert any("демо-режим" in edit for edit in msg.sent[0].edits)
    assert msg.audios[0].filename == "Демо.mp3"
    assert state.cleared


async def test_cancelled_generation_deletes_status_and_unsets_flag(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Честная отмена генерации (строки 121–126).

    Покрывает обработчик CancelledError в generate_and_send: сообщение
    прогресса удаляется, флаг generating снимается (gen_id совпадает —
    отмена пришла раньше нового состояния), CancelledError пробрасывается
    дальше, задача снимается с реестра. Задача запускается явно через
    create_task, чтобы отменить её извне, как это делает base_handlers.cmd_cancel.
    """
    await _make_authorized_user(patched_auth_db, 62)

    async def hanging_generate(prompt, on_progress=None):
        await asyncio.sleep(60)

    monkeypatch.setattr(service_pipeline, "generate_song_real", hanging_generate)

    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="Отмена", uid=62)

    task = asyncio.create_task(handlers_generation.handle_title(msg, state))
    # Ждём, пока задача зарегистрируется в реестре и начнёт генерацию
    for _ in range(100):
        if 62 in registry and not registry[62].done():
            break
        await asyncio.sleep(0.01)

    registry[62].cancel()  # то же, что делает cmd_cancel_generation
    with pytest.raises(asyncio.CancelledError):
        await task

    assert msg.sent[0].deleted  # сообщение прогресса удалено
    data = await state.get_data()
    assert data["generating"] is False
    assert 62 not in registry


async def test_generation_failure_notifies_owner(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, monkeypatch
):
    """Сбой генерации уведомляет владельца (строки 134–140).

    При исключении в сервисе notify_owner вызывается один раз с
    контекстом, содержащим user_id, и самим исключением; после чего
    пользователю остаётся кнопка ретрая.
    """
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
    """Название из одних пробелов отклоняется (строка 287–288).

    Пустое (после strip) название не сохраняется в FSM, состояние
    остаётся waiting_for_title, генерация не запускается.
    """
    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="   ", uid=64)

    await handlers_generation.handle_title(msg, state)

    assert "непустое название" in msg.answers[-1]
    assert state.state is None


async def test_handle_title_rejects_too_long_title(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Слишком длинное название отклоняется (строка 287–288).

    Название длиннее MAX_TITLE_LEN отвергается с указанием лимита;
    FSM-состояние не меняется.
    """
    state = fake_state()
    await state.update_data(prompt="промпт")
    msg = make_message(text="а" * (handlers_generation.MAX_TITLE_LEN + 1), uid=65)

    await handlers_generation.handle_title(msg, state)

    assert "Слишком длинное название" in msg.answers[-1]
    assert state.state is None


async def test_handle_title_blocked_while_generating(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """handle_title не запускает генерацию при активной (строки 324–325).

    Внешняя проверка флага generating: пользователь получает просьбу
    подождать, title в FSM не сохраняется.
    """
    state = fake_state()
    await state.update_data(prompt="промпт", generating=True)
    msg = make_message(text="Название", uid=66)

    await handlers_generation.handle_title(msg, state)

    assert "Дождитесь окончания" in msg.answers[-1]
    # title не сохранился и генерация не запускалась
    assert "title" not in (await state.get_data())


async def test_handle_title_missing_prompt_suggests_restart(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state
):
    """Генерация без сохранённого промпта не запускается (строки 179–184).

    Если промпт потерялся из FSM (перезапуск бота, гонка кнопок, чистка),
    пользователь получает предложение начать заново, FSM очищается,
    генерация не запускается.
    """
    state = fake_state()  # промпта нет
    msg = make_message(text="Название", uid=73)

    await handlers_generation.handle_title(msg, state)

    assert "Описание песни потерялось" in msg.answers[-1]
    assert state.cleared
    assert len(msg.audios) == 0


async def test_retry_generation_blocked_while_generating(
    patched_auth_db, clean_auth_state, clean_generation_registry, make_message, fake_state, make_callback
):
    """Ретрай заблокирован во время идущей генерации (строки 327–328).

    Устаревшая кнопка повтора при активной генерации отвечает alert
    «Генерация уже идёт.» и не запускает вторую генерацию.
    """
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
    """Успешный ретрай после сбоя (строки 364–365, 377–381).

    Покрывает «счастливый путь» retry_generation: старое сообщение с
    кнопкой удаляется, callback.answer() закрывается без текста,
    генерация запускается с сохранёнными prompt/title и завершается
    отправкой аудио; FSM очищается.
    """
    await _make_authorized_user(patched_auth_db, 68)

    async def fake_generate(prompt, on_progress=None):
        return b"retry-audio"

    _install_generation(monkeypatch, fake_generate)

    state = fake_state()
    await state.update_data(prompt="промпт", title="Ретрай")
    msg = make_message(uid=68)
    callback = make_callback(68, msg)

    await handlers_generation.retry_generation(callback, state)

    assert msg.deleted  # старое сообщение с кнопкой удалено
    assert callback.answered[-1] == (None, False)  # callback.answer()
    assert len(msg.audios) == 1
    assert msg.audios[0].filename == "Ретрай.mp3"
    assert state.cleared
