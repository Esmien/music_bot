"""Юнит-тесты кастомных фильтров роутера."""

import pytest
from aiogram.types import Message, User

from handlers.filters import IsPendingAuth, NotCommand
from fsm.evaluation_fsm import add_pending_auth

pytestmark = pytest.mark.unit


@pytest.fixture
def clean_pending_auth(fake_redis):
    """pending_auth пуст: fake_redis из conftest чист при создании."""
    yield


@pytest.mark.parametrize(
    "text, expected",
    [
        pytest.param("/start", False, id="command"),
        pytest.param("/help ", False, id="command-with-trailing-space"),
        pytest.param("привет", True, id="plain-text"),
        pytest.param("", True, id="empty-text"),
        pytest.param(None, True, id="no-text"),
    ],
)
async def test_not_command(text, expected):
    """NotCommand пропускает любой текст, кроме начинающегося с '/'.

    Отсутствие текста (None) тоже считается не-командой.
    """
    message = Message.model_construct(text=text)
    assert await NotCommand()(message) is expected


@pytest.mark.usefixtures("clean_pending_auth")
@pytest.mark.parametrize(
    "pending_uid, checked_uid, expected",
    [
        pytest.param(1, 1, True, id="waiting-for-key"),
        pytest.param(1, 2, False, id="another-user-not-in-pending"),
        pytest.param(None, 1, False, id="nobody-in-pending"),
    ],
)
async def test_is_pending_auth(pending_uid, checked_uid, expected):
    # pending_uid — кто добавлен в ожидание, checked_uid — чей апдейт проверяем
    if pending_uid is not None:
        await add_pending_auth(pending_uid)
    message = Message.model_construct(from_user=User(id=checked_uid, is_bot=False, first_name="T"))
    assert await IsPendingAuth()(message) is expected


async def test_is_pending_auth_without_from_user():
    # Служебные апдейты бывают без from_user — фильтр не должен падать
    message = Message.model_construct(from_user=None)
    assert await IsPendingAuth()(message) is False
