"""Интеграционные тесты авторизации: хендлеры против реальной тестовой БД."""

import pytest

import config
from handlers import auth as handlers_auth
from handlers.state import pending_auth
from models import User

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "is_authorized_flag, expected",
    [
        pytest.param(True, True, id="authorized"),
        pytest.param(False, False, id="logged-out"),
    ],
)
async def test_is_authorized_existing_user(patched_auth_db, is_authorized_flag, expected):
    async with patched_auth_db() as session:
        session.add(User(tg_id=42, is_authorized=is_authorized_flag))
        await session.commit()

    assert await handlers_auth.is_authorized(42) is expected


async def test_is_authorized_unknown_user(patched_auth_db):
    assert await handlers_auth.is_authorized(999) is False


async def test_handle_key_accepts_valid_key(patched_auth_db, clean_auth_state, make_message):
    msg = make_message(text="secret-key", uid=100)

    await handlers_auth.handle_key(msg)

    # Сообщение с ключом не должно оставаться в истории чата
    assert msg.deleted
    assert any("успешно авторизованы" in answer for answer in msg.answers)
    assert await handlers_auth.is_authorized(100)


async def test_handle_key_reauthorizes_existing_user(patched_auth_db, clean_auth_state, make_message):
    # Пользователь уже есть в БД, но разлогинен — должна сработать ветка обновления
    async with patched_auth_db() as session:
        session.add(User(tg_id=200, is_authorized=False))
        await session.commit()

    msg = make_message(text="secret-key", uid=200)
    await handlers_auth.handle_key(msg)

    assert await handlers_auth.is_authorized(200)
    assert any("успешно авторизованы" in answer for answer in msg.answers)


@pytest.mark.parametrize("attempt", [1, 2, 4])
async def test_handle_key_counts_failed_attempts(patched_auth_db, clean_auth_state, make_message, attempt):
    msg = make_message(text="wrong-key", uid=301)
    for _ in range(attempt):
        await handlers_auth.handle_key(msg)

    assert handlers_auth.failed_key_attempts[301] == attempt
    assert "Неверный ключ доступа." in msg.answers[-1]
    assert await handlers_auth.is_authorized(301) is False


async def test_handle_key_blocks_after_max_attempts(patched_auth_db, clean_auth_state, make_message):
    msg = make_message(text="wrong-key", uid=302)
    for _ in range(handlers_auth.MAX_KEY_ATTEMPTS):
        await handlers_auth.handle_key(msg)

    assert "Слишком много неверных попыток" in msg.answers[-1]
    # После блокировки счётчик и статус ожидания должны быть сброшены
    assert 302 not in handlers_auth.failed_key_attempts
    assert 302 not in pending_auth
    assert await handlers_auth.is_authorized(302) is False


async def test_handle_key_without_configured_key(patched_auth_db, clean_auth_state, make_message, monkeypatch):
    monkeypatch.setattr(config, "BOT_ACCESS_KEY", "")
    msg = make_message(text="secret-key", uid=303)

    await handlers_auth.handle_key(msg)

    assert "Бот не настроен" in msg.answers[-1]
    assert await handlers_auth.is_authorized(303) is False


async def test_cmd_logout_revokes_access(patched_auth_db, clean_auth_state, make_message, fake_state):
    async with patched_auth_db() as session:
        session.add(User(tg_id=42, is_authorized=True))
        await session.commit()

    msg = make_message(uid=42)
    state = fake_state()
    await handlers_auth.cmd_logout(msg, state)

    assert state.cleared
    assert await handlers_auth.is_authorized(42) is False
    assert "Вы вышли" in msg.answers[-1]
