"""Интеграционные тесты авторизации: хендлеры против реальной тестовой БД."""

import pytest

from config import settings
from database.models import User
from fsm.evaluation_fsm import add_pending_auth, is_pending_auth
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
