"""Интеграционные тесты раздела «Кредиты»: реальная БД, OpenRouter замокан."""

import pytest

from handlers import credits as handlers_credits
from models import User

pytestmark = pytest.mark.integration


class FakeKeyInfoResponse:
    """Заглушка ответа GET https://openrouter.ai/api/v1/key."""

    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return {"data": self._data}


class FakeAsyncClient:
    """Заглушка httpx.AsyncClient: get() отдаёт готовый ответ или кидает исключение."""

    def __init__(self, response, **kwargs):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.fixture
def patch_key_info(monkeypatch):
    """Подменяет httpx.AsyncClient в хендлере кредитов на заглушку."""

    def _install(response):
        monkeypatch.setattr(
            handlers_credits.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(response)
        )

    return _install


async def _make_authorized_user(sessionmaker, tg_id: int) -> None:
    async with sessionmaker() as session:
        session.add(User(tg_id=tg_id, is_authorized=True))
        await session.commit()


async def test_cmd_credits_counts_songs(
    patched_auth_db, clean_auth_state, make_message, fake_state, patch_key_info
):
    # SONG_PRICE=0.5 из тестового окружения: одна генерация стоит 0.5$
    await _make_authorized_user(patched_auth_db, tg_id=7)
    patch_key_info(FakeKeyInfoResponse(data={"limit": 5.0, "usage": 1.5, "limit_remaining": 3.5}))

    msg = make_message(uid=7)
    await handlers_credits.cmd_credits(msg, fake_state())

    text = msg.answers[0]
    assert "Всего доступно генераций: 10" in text
    assert "Сгенерировано композиций: 3" in text
    assert "Доступное количество генераций: 7" in text


async def test_cmd_credits_without_limit(
    patched_auth_db, clean_auth_state, make_message, fake_state, patch_key_info
):
    await _make_authorized_user(patched_auth_db, tg_id=8)
    patch_key_info(FakeKeyInfoResponse(data={"usage": 1.0}))

    msg = make_message(uid=8)
    await handlers_credits.cmd_credits(msg, fake_state())

    text = msg.answers[0]
    assert "Всего доступно генераций: Без лимита" in text
    assert "Сгенерировано композиций: 2" in text
    assert "Пока не кончится бабосик" in text


@pytest.mark.parametrize("status_code", [401, 500])
async def test_cmd_credits_api_error_status(
    patched_auth_db, clean_auth_state, make_message, fake_state, patch_key_info, status_code
):
    await _make_authorized_user(patched_auth_db, tg_id=9)
    patch_key_info(FakeKeyInfoResponse(status_code=status_code))

    msg = make_message(uid=9)
    await handlers_credits.cmd_credits(msg, fake_state())

    assert f"Ошибка запроса: {status_code}" in msg.answers[0]


async def test_cmd_credits_network_failure(
    patched_auth_db, clean_auth_state, make_message, fake_state, patch_key_info
):
    await _make_authorized_user(patched_auth_db, tg_id=10)
    patch_key_info(RuntimeError("connection refused"))

    msg = make_message(uid=10)
    await handlers_credits.cmd_credits(msg, fake_state())

    assert "Не получилось проверить остатки" in msg.answers[0]
