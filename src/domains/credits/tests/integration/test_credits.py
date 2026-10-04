"""Интеграционные тесты раздела «Кредиты»: реальная БД, OpenRouter замокан."""

import httpx
import pytest

from core.database import User
from domains.credits import handlers as handlers_credits
from domains.credits import service as credits_service

pytestmark = pytest.mark.integration


class FakeKeyInfoResponse:
    """Заглушка ответа GET https://openrouter.ai/api/v1/key."""

    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.headers = headers or {}

    def json(self):
        return {"data": self._data}


class FakeAsyncClient:
    """Заглушка httpx.AsyncClient: get() отдаёт готовый ответ или кидает исключение."""

    def __init__(self, response, **kwargs):
        self._response = response
        self.calls_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        self.calls_count += 1
        if isinstance(self._response, list):
            item = self._response.pop(0) if self._response else self._response[-1]
            if isinstance(item, Exception):
                raise item
            return item
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.fixture
def patch_key_info(monkeypatch):
    """Подменяет httpx.AsyncClient в сервисе кредитов на заглушку."""

    def _install(response):
        monkeypatch.setattr(credits_service.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(response))

    return _install


async def _make_authorized_user(sessionmaker, tg_id: int) -> None:
    async with sessionmaker() as session:
        session.add(User(tg_id=tg_id, is_authorized=True))
        await session.commit()


async def test_cmd_credits_counts_songs(patched_auth_db, clean_auth_state, make_message, fake_state, patch_key_info):
    """Баланс пересчитывается из долларов в количество песен.

    SONG_PRICE=0.5 из тестового окружения: одна генерация стоит 0.5$,
    поэтому лимит 5.0$ → 10 песен, трата 1.5$ → 3 песни, остаток 3.5$ → 7.
    """
    await _make_authorized_user(patched_auth_db, tg_id=7)
    patch_key_info(FakeKeyInfoResponse(data={"limit": 5.0, "usage": 1.5, "limit_remaining": 3.5}))

    summary = await credits_service.get_credits_summary(api_key="test_key", song_price=0.5)

    assert summary.status_code == 200
    assert summary.total_songs == 10
    assert summary.used_songs == 3
    assert summary.remaining_songs == 7


async def test_cmd_credits_without_limit(patched_auth_db, clean_auth_state, make_message, fake_state, patch_key_info):
    """API без поля limit считается безлимитным.

    Если OpenRouter не вернул limit, total показывается как
    «Без лимита», а остаток посчитать нельзя — пользователь видит
    заглушку «Невозможно посчитать».
    """
    await _make_authorized_user(patched_auth_db, tg_id=8)
    patch_key_info(FakeKeyInfoResponse(data={"usage": 1.0}))

    summary = await credits_service.get_credits_summary(api_key="test_key", song_price=0.5)

    assert summary.status_code == 200
    assert summary.total_songs == "Без лимита"
    assert summary.used_songs == 2
    assert summary.remaining_songs == "Невозможно посчитать"


@pytest.mark.parametrize("status_code", [401, 500])
async def test_cmd_credits_api_error_status(
    patched_auth_db, clean_auth_state, make_message, fake_state, patch_key_info, status_code
):
    await _make_authorized_user(patched_auth_db, tg_id=9)
    patch_key_info(FakeKeyInfoResponse(status_code=status_code))

    summary = await credits_service.get_credits_summary(api_key="test_key", song_price=0.5)

    assert summary.status_code == status_code
    assert summary.total_songs is None


async def test_cmd_credits_network_failure(patched_auth_db, clean_auth_state, make_message, fake_state, patch_key_info):
    await _make_authorized_user(patched_auth_db, tg_id=10)
    patch_key_info(httpx.ConnectError("connection refused"))

    with pytest.raises(httpx.ConnectError):
        await credits_service.get_credits_summary(api_key="test_key", song_price=0.5)


async def test_cmd_credits_401_no_retry(patched_auth_db, clean_auth_state, monkeypatch):
    """Ошибки авторизации 401 не повторяются."""
    client = FakeAsyncClient(FakeKeyInfoResponse(status_code=401))
    monkeypatch.setattr(credits_service.httpx, "AsyncClient", lambda **kwargs: client)

    summary = await credits_service.get_credits_summary(api_key="bad_key", song_price=0.5)

    assert summary.status_code == 401
    assert client.calls_count == 1


async def test_cmd_credits_429_retries_and_succeeds(patched_auth_db, clean_auth_state, monkeypatch):
    """HTTP 429 повторяется и при успешной следующей попытке возвращает данные."""

    # Быстрый wait для тестов — важно, что это НЕ корутина, а обычная функция
    def _instant_wait(retry_state):
        return 0.0

    monkeypatch.setattr(credits_service, "default_retry_wait", _instant_wait)

    responses = [
        FakeKeyInfoResponse(status_code=429, headers={"Retry-After": "0.01"}),
        FakeKeyInfoResponse(status_code=200, data={"limit": 10.0, "usage": 2.0, "limit_remaining": 8.0}),
    ]
    client = FakeAsyncClient(responses)
    monkeypatch.setattr(credits_service.httpx, "AsyncClient", lambda **kwargs: client)

    summary = await credits_service.get_credits_summary(api_key="test_key", song_price=0.5)

    assert summary.status_code == 200
    assert summary.total_songs == 20
    assert client.calls_count == 2


async def test_cmd_credits_without_api_key(patched_auth_db, clean_auth_state, make_message, fake_state, monkeypatch):
    """Без OPENROUTER_API_KEY хендлер сразу отправляет сообщение об ошибке."""
    from core.config import settings

    await _make_authorized_user(patched_auth_db, tg_id=11)

    # Очищаем ключ API
    monkeypatch.setattr(settings.bot, "OPENROUTER_API_KEY", None)

    msg = make_message(uid=11)
    await handlers_credits.cmd_credits(msg, fake_state())

    assert len(msg.answers) == 1
    assert "бот не настроен" in msg.answers[0].lower()


@pytest.mark.parametrize(
    ("state_name", "state_data"),
    [
        ("PromptEnricherStates:waiting_idea", {"idea": "rock ballad"}),
        ("GenerationStates:generating", {"gen_id": 42, "title": "Epic Track"}),
        ("FeedbackStates:waiting_feedback", {"gen_id": 42, "score": 1}),
    ],
)
async def test_cmd_credits_preserves_fsm_state_and_data(
    patched_auth_db,
    clean_auth_state,
    make_message,
    fake_state,
    patch_key_info,
    state_name,
    state_data,
):
    """Вызов /credits сохраняет FSM state и FSM data (включая gen_id) пользователя."""
    await _make_authorized_user(patched_auth_db, tg_id=12)
    patch_key_info(FakeKeyInfoResponse(data={"limit": 5.0, "usage": 1.0, "limit_remaining": 4.0}))

    state = fake_state()
    await state.set_state(state_name)
    await state.update_data(**state_data)

    msg = make_message(uid=12)
    await handlers_credits.cmd_credits(msg, state)

    assert len(msg.answers) == 1
    assert not state.cleared
    assert state.state == state_name
    current_data = await state.get_data()
    assert current_data == state_data
    if "gen_id" in state_data:
        assert current_data.get("gen_id") == state_data["gen_id"]
