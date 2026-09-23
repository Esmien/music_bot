"""Интеграционные тесты авторизации: хендлеры против реальной тестовой БД."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import User
from fsm.registries.auth_registry import add_pending_auth, is_pending_auth
from handlers import auth as handlers_auth

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "is_authorized_flag, expected",
    [
        pytest.param(True, True, id="authorized"),
        pytest.param(False, False, id="logged-out"),
    ],
)
async def test_is_authorized_existing_user(patched_auth_db, is_authorized_flag, expected):
    """is_authorized возвращает флаг из БД для существующего пользователя.

    Параметризовано: авторизованный (True) и разлогиненный (False).
    """
    async with patched_auth_db() as session:
        session.add(User(tg_id=42, is_authorized=is_authorized_flag))
        await session.commit()

    assert await handlers_auth.is_authorized(42) is expected


async def test_is_authorized_unknown_user(patched_auth_db):
    """Пользователя нет в БД — авторизации нет (не падаем на None)."""
    assert await handlers_auth.is_authorized(999) is False


async def test_handle_key_accepts_valid_key(patched_auth_db, clean_auth_state, make_message):
    """Верный ключ авторизует нового пользователя и удаляет сообщение с ключом.

    Ключ не должен остаться в истории чата — проверяем msg.deleted.
    """
    msg = make_message(text="secret-key", uid=100)

    await handlers_auth.handle_key(msg)

    # Сообщение с ключом не должно оставаться в истории чата
    assert msg.deleted
    assert any("успешно авторизованы" in answer for answer in msg.answers)
    assert await handlers_auth.is_authorized(100)


async def test_handle_key_reauthorizes_existing_user(patched_auth_db, clean_auth_state, make_message):
    """Повторный вход существующего разлогиненного пользователя.

    Должна сработать ветка обновления (is_authorized=True у существующей
    записи), а не создание дубликата.
    """
    async with patched_auth_db() as session:
        session.add(User(tg_id=200, is_authorized=False))
        await session.commit()

    msg = make_message(text="secret-key", uid=200)
    await handlers_auth.handle_key(msg)

    assert await handlers_auth.is_authorized(200)
    assert any("успешно авторизованы" in answer for answer in msg.answers)


@pytest.mark.parametrize("attempt", [1, 2, 4])
async def test_handle_key_counts_failed_attempts(patched_auth_db, clean_auth_state, make_message, attempt):
    """Неверные попытки считаются, но блокировка ещё не срабатывает.

    До MAX_KEY_ATTEMPTS счётчик растёт, пользователь получает отказ,
    но остаётся в pending_auth и может попробовать снова.
    """
    msg = make_message(text="wrong-key", uid=301)
    for _ in range(attempt):
        await handlers_auth.handle_key(msg)

    assert handlers_auth.failed_key_attempts[301] == attempt
    assert "Неверный ключ доступа." in msg.answers[-1]
    assert await handlers_auth.is_authorized(301) is False


async def test_handle_key_blocks_after_max_attempts(patched_auth_db, clean_auth_state, make_message):
    """После MAX_KEY_ATTEMPTS неверных попыток пользователь выбывает.

    Защита от перебора ключа: счётчик и статус ожидания сбрасываются,
    продолжить можно только через /start.
    """
    msg = make_message(text="wrong-key", uid=302)
    for _ in range(handlers_auth.MAX_KEY_ATTEMPTS):
        await handlers_auth.handle_key(msg)

    assert "Слишком много неверных попыток" in msg.answers[-1]
    # После блокировки счётчик и статус ожидания должны быть сброшены
    assert 302 not in handlers_auth.failed_key_attempts
    assert not await is_pending_auth(302)
    assert await handlers_auth.is_authorized(302) is False


async def test_handle_key_without_configured_key(patched_auth_db, clean_auth_state, make_message, monkeypatch):
    """Без настроенного BOT_ACCESS_KEY авторизация невозможна.

    Пользователь получает сообщение о ненастроенном боте, авторизация
    не выдаётся даже с формально верным ключом.
    """
    monkeypatch.setattr(settings.bot, "BOT_ACCESS_KEY", "")
    msg = make_message(text="secret-key", uid=303)

    await handlers_auth.handle_key(msg)

    assert "Бот не настроен" in msg.answers[-1]
    assert await handlers_auth.is_authorized(303) is False


async def test_cmd_logout_revokes_access(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Logout снимает авторизацию в БД и очищает FSM."""
    async with patched_auth_db() as session:
        session.add(User(tg_id=42, is_authorized=True))
        await session.commit()

    msg = make_message(uid=42)
    state = fake_state()
    await handlers_auth.cmd_logout(msg, state)

    assert state.cleared
    assert await handlers_auth.is_authorized(42) is False
    assert "Вы вышли" in msg.answers[-1]


async def test_cmd_start_greets_authorized(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Авторизованный получает приветствие с клавиатурой, не попадая в pending_auth."""
    async with patched_auth_db() as session:
        session.add(User(tg_id=42, is_authorized=True))
        await session.commit()

    msg = make_message(uid=42)
    state = fake_state()
    await handlers_auth.cmd_start(msg, state)

    assert "Используйте кнопки ниже" in msg.answers[-1]
    assert state.cleared
    assert not await is_pending_auth(42)


async def test_cmd_start_puts_unauthorized_into_pending(patched_auth_db, clean_auth_state, make_message, fake_state):
    """Неавторизованный попадает в pending_auth для ввода ключа.

    Дальше его текст перехватит handle_key через фильтр IsPendingAuth.
    """
    msg = make_message(uid=43)
    await handlers_auth.cmd_start(msg, fake_state())

    assert "отправьте ключ доступа" in msg.answers[-1]
    assert await is_pending_auth(43)


async def test_require_auth_hints_unauthorized(patched_auth_db, clean_auth_state, make_message):
    """_require_auth отклоняет ожидающего ключ и подсказывает, что делать."""
    await add_pending_auth(44)
    msg = make_message(uid=44)

    assert await handlers_auth._require_auth(msg) is False
    assert "Требуется ключ доступа. Нажмите /start, чтобы ввести" in msg.answers[-1]


async def test_fallback_skips_users_waiting_for_key(patched_auth_db, clean_auth_state, make_message):
    """Fallback молчит для ожидающих ввод ключа — сообщение уйдёт в handle_key."""
    await add_pending_auth(45)
    msg = make_message(text="что-то", uid=45)

    await handlers_auth.fallback(msg)

    assert msg.answers == []  # сообщение должно уйти в handle_key


async def test_fallback_hints_unauthorized(patched_auth_db, clean_auth_state, make_message):
    """Неавторизованному fallback напоминает про /start и ключ доступа."""
    msg = make_message(text="что-то", uid=46)

    await handlers_auth.fallback(msg)

    assert "Сначала /start" in msg.answers[-1]


async def test_fallback_hints_authorized(patched_auth_db, clean_auth_state, make_message):
    """Авторизованному fallback подсказывает пользоваться кнопками."""
    async with patched_auth_db() as session:
        session.add(User(tg_id=47, is_authorized=True))
        await session.commit()

    msg = make_message(text="что-то", uid=47)
    await handlers_auth.fallback(msg)

    assert "Не понял" in msg.answers[-1]


async def test_handle_key_survives_delete_failure(patched_auth_db, clean_auth_state, make_message):
    """Сбой удаления сообщения с ключом не ломает авторизацию.

    У бота может не быть прав на удаление — хендлер логирует warning
    и продолжает работу.
    """
    msg = make_message(text="secret-key", uid=48)
    msg.fail_delete = True

    await handlers_auth.handle_key(msg)

    assert not msg.deleted
    assert any("успешно авторизованы" in answer for answer in msg.answers)
    assert await handlers_auth.is_authorized(48)


async def _make_user(sessionmaker, tg_id: int, is_authorized: bool) -> None:
    """Создаёт пользователя с заданным статусом авторизации.

    Args:
        sessionmaker: Фабрика сессий тестовой БД.
        tg_id: Telegram user_id.
        is_authorized: Значение флага is_authorized.
    """
    async with sessionmaker() as session:
        session.add(User(tg_id=tg_id, is_authorized=is_authorized))
        await session.commit()


async def _get_user(sessionmaker, tg_id: int) -> User | None:
    """Читает пользователя из тестовой БД.

    Args:
        sessionmaker: Фабрика сессий тестовой БД.
        tg_id: Telegram user_id.

    Returns:
        Найденный User или None.
    """
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        return result.scalar_one_or_none()


async def test_mark_user_authorized_recovers_after_integrity_error(patched_auth_db, monkeypatch):
    """Гонка вставок: первый session.get не видит пользователя, flush падает с IntegrityError.

    Пользователь уже есть в БД (например, ранее выходил), но первый session.get
    его «не находит» — имитация параллельной вставки того же tg_id.
    После IntegrityError на flush функция откатывается, перечитывает запись
    через select и проставляет ей is_authorized=True.
    """
    await _make_user(patched_auth_db, tg_id=21, is_authorized=False)

    real_select = handlers_auth.select
    select_calls = {"count": 0}

    def fake_select(*entities, **kwargs):
        select_calls["count"] += 1
        return real_select(*entities, **kwargs)

    monkeypatch.setattr(handlers_auth, "select", fake_select)

    # Первый session.get «не видит» пользователя — имитация гонки вставок
    real_get = AsyncSession.get
    get_calls = {"count": 0}

    async def fake_get(self, entity, *args, **kwargs):
        get_calls["count"] += 1
        if get_calls["count"] == 1:
            return None
        return await real_get(self, entity, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", fake_get)

    await handlers_auth._mark_user_authorized(uid=21)

    # select вызван один раз — перечитывание после IntegrityError на flush
    assert select_calls["count"] == 1
    db_user = await _get_user(patched_auth_db, tg_id=21)
    assert db_user is not None
    assert db_user.is_authorized is True


async def test_cmd_logout_db_error_notifies_owner(patched_auth_db, make_message, fake_state, monkeypatch):
    """Сбой БД при logout: пользователь остаётся авторизован, владелец уведомлён.

    SessionLocal бросает SQLAlchemyError — срабатывает ветка except:
    сообщение «Не удалось выйти», вызов notify_owner, ранний выход
    без очистки FSM и без снятия авторизации.
    """
    await _make_user(patched_auth_db, tg_id=22, is_authorized=True)

    def _failing_session_factory():
        raise SQLAlchemyError("database is down")

    monkeypatch.setattr(handlers_auth, "SessionLocal", _failing_session_factory)

    notify_calls = []

    async def fake_notify_owner(bot, context, err):
        notify_calls.append((bot, context, err))

    monkeypatch.setattr(handlers_auth, "notify_owner", fake_notify_owner)

    msg = make_message(uid=22)
    state = fake_state()
    await handlers_auth.cmd_logout(msg, state)

    assert "Не удалось выйти" in msg.answers[0]
    assert len(notify_calls) == 1
    bot, context, err = notify_calls[0]
    assert bot is msg.bot
    assert "22" in context
    assert isinstance(err, SQLAlchemyError)

    # ранний выход: FSM не очищен, авторизация в БД не снята
    assert state.cleared is False
    db_user = await _get_user(patched_auth_db, tg_id=22)
    assert db_user is not None
    assert db_user.is_authorized is True


async def test_cmd_logout_cancels_active_generation(patched_auth_db, make_message, fake_state, fake_redis, monkeypatch):
    """Logout гасит живую генерацию и полностью разавторизовывает пользователя.

    Активная задача из active_tasks отменяется, пользователь снимается
    с ожидания ключа, FSM очищается, is_authorized=False в БД.
    """
    await _make_user(patched_auth_db, tg_id=23, is_authorized=True)
    await add_pending_auth(uid=23)

    class FakeTask:
        def __init__(self):
            self.cancel_calls = 0

        def done(self):
            return False

        def cancel(self):
            self.cancel_calls += 1

    task = FakeTask()
    monkeypatch.setattr(handlers_auth, "active_tasks", {23: task})

    msg = make_message(uid=23)
    state = fake_state()
    await handlers_auth.cmd_logout(msg, state)

    assert task.cancel_calls == 1
    assert "Вы вышли" in msg.answers[0]
    assert state.cleared is True
    assert not await is_pending_auth(uid=23)

    db_user = await _get_user(patched_auth_db, tg_id=23)
    assert db_user is not None
    assert db_user.is_authorized is False


async def test_cmd_logout_skips_cancel_for_finished_task(
    patched_auth_db, make_message, fake_state, fake_redis, monkeypatch
):
    """Завершённая задача генерации не отменяется повторно."""
    await _make_user(patched_auth_db, tg_id=24, is_authorized=True)

    class FakeTask:
        def __init__(self):
            self.cancel_calls = 0

        def done(self):
            return True

        def cancel(self):
            self.cancel_calls += 1

    task = FakeTask()
    monkeypatch.setattr(handlers_auth, "active_tasks", {24: task})

    msg = make_message(uid=24)
    await handlers_auth.cmd_logout(msg, fake_state())

    assert task.cancel_calls == 0
    assert "Вы вышли" in msg.answers[0]
