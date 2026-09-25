"""Юнит-тесты сервиса обогащения промпта (domains/enricher/service.py).

Внешний API обогатителя обязательно мокается: тестируется логика
клиента, парсинга и форматирования, а не сеть.
"""

import json

import httpx
import pytest

from core.config import settings
from domains.enricher import service as enricher


class FakeResponse:
    """Заглушка httpx.Response: raise_for_status() кидает исключение или json() отдаёт payload."""

    def __init__(self, payload=None, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Заглушка httpx.AsyncClient: post() отдаёт готовый ответ или кидает исключение."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json=None, headers=None):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture
def patch_enricher_client(monkeypatch):
    """Подменяет httpx.AsyncClient в domains.enricher.service на фабрику заглушек.

    Возвращает функцию-фабрику: patch_enricher_client(response=..., error=...).
    """

    def _patch(response=None, error=None):
        client = FakeAsyncClient(response=response, error=error)
        monkeypatch.setattr(enricher.httpx, "AsyncClient", lambda **kwargs: client)
        return client

    return _patch


def _api_response(content: str) -> FakeResponse:
    return FakeResponse(payload={"choices": [{"message": {"content": content}}]})


async def test_enrich_prompt_returns_content(patch_enricher_client):
    client = patch_enricher_client(response=_api_response("enriched text"))

    result = await enricher.enrich_prompt(prompt="idea")

    assert result == "enriched text"
    assert len(client.post_calls) == 1


async def test_enrich_prompt_sends_payload_and_headers(patch_enricher_client):
    client = patch_enricher_client(response=_api_response("ok"))

    await enricher.enrich_prompt(prompt="idea", history=[{"role": "assistant", "content": "prev"}])

    call = client.post_calls[0]
    assert call["url"] == settings.enrich.ENRICH_URL
    assert call["headers"] == {"Authorization": f"Bearer {settings.enrich.ENRICH_TOKEN}"}
    assert call["json"]["model"] == settings.enrich.ENRICH_MODEL
    assert call["json"]["stream"] is False
    # История идёт перед текущим сообщением пользователя
    assert call["json"]["messages"] == [
        {"role": "assistant", "content": "prev"},
        {"role": "user", "content": "idea"},
    ]


async def test_enrich_prompt_without_history_sends_only_user_message(patch_enricher_client):
    client = patch_enricher_client(response=_api_response("ok"))

    await enricher.enrich_prompt(prompt="idea")

    assert client.post_calls[0]["json"]["messages"] == [{"role": "user", "content": "idea"}]


async def test_enrich_prompt_network_error_returns_none(patch_enricher_client):
    patch_enricher_client(error=httpx.ConnectError("connection refused"))

    result = await enricher.enrich_prompt(prompt="idea")

    assert result is None


async def test_enrich_prompt_http_status_error_returns_none(patch_enricher_client):
    error = httpx.HTTPStatusError("500", request=httpx.Request("POST", "http://test"), response=None)
    patch_enricher_client(response=FakeResponse(payload={}, status_error=error))

    result = await enricher.enrich_prompt(prompt="idea")

    assert result is None


async def test_enrich_prompt_empty_content_returns_none(patch_enricher_client):
    patch_enricher_client(response=FakeResponse(payload={"choices": [{"message": {"content": "   "}}]}))

    result = await enricher.enrich_prompt(prompt="idea")

    assert result is None


async def test_enrich_prompt_unexpected_body_returns_none(patch_enricher_client):
    patch_enricher_client(response=FakeResponse(payload={"unexpected": True}))

    result = await enricher.enrich_prompt(prompt="idea")

    assert result is None


async def test_enrich_prompt_raises_without_config(monkeypatch):
    monkeypatch.setattr(settings.enrich, "ENRICH_URL", "")
    monkeypatch.setattr(settings.enrich, "ENRICH_MODEL", "")

    with pytest.raises(ValueError, match="Enricher URL or model is not configured"):
        await enricher.enrich_prompt(prompt="idea")


def test_format_enriched_prompt_full_json():
    raw = json.dumps(
        {
            "genre_and_style": ["synthwave", "retro"],
            "mood": "energetic",
            "instrumentation": ["synth", "drums"],
            "tempo_bpm": 120,
            "vocal_style": "male vocal",
            "language": "russian",
            "lyrics": "Текст песни",
            "song_structure": ["[Intro]", "[Verse 1]", "[Chorus]"],
        }
    )

    result = enricher.format_enriched_prompt(raw)

    assert "🎵 Жанр и стиль:\nsynthwave, retrowave" in result or "🎵 Жанр и стиль:\nsynthwave, retro" in result
    assert "🎭 Настроение:\nenergetic" in result
    assert "⏱ Темп:\n120 BPM" in result
    assert "📝 Текст песни:\nТекст песни" in result
    # Структура песни — построчно
    assert "🎼 Структура:\n[Intro]\n[Verse 1]\n[Chorus]" in result


def test_format_enriched_prompt_skips_empty_fields():
    raw = json.dumps({"genre_and_style": "rock", "mood": "", "instrumentation": [], "tempo_bpm": None})

    result = enricher.format_enriched_prompt(raw)

    assert "Жанр и стиль" in result
    assert "Настроение" not in result
    assert "Инструменты" not in result
    assert "Темп" not in result


def test_format_enriched_prompt_invalid_json_returns_raw():
    raw = "Просто текст вместо JSON"

    result = enricher.format_enriched_prompt(raw)

    assert result == raw


def test_format_enriched_prompt_strips_markdown_fence():
    raw = '```json\n{"mood": "calm"}\n```'

    result = enricher.format_enriched_prompt(raw)

    assert result == "🎭 Настроение:\ncalm"


def test_format_enriched_prompt_empty_json_returns_raw():
    raw = json.dumps({})

    result = enricher.format_enriched_prompt(raw)

    assert result == raw


def test_parse_enricher_json_strips_fence_without_language():
    raw = '```\n{"mood": "calm"}\n```'

    data = enricher._parse_enricher_json(raw=raw)

    assert data == {"mood": "calm"}


def test_parse_enricher_json_plain_json():
    data = enricher._parse_enricher_json(raw='{"mood": "calm"}')

    assert data == {"mood": "calm"}


def test_parse_enricher_json_invalid_json_raises():
    with pytest.raises(json.JSONDecodeError):
        enricher._parse_enricher_json(raw="not a json")


def test_parse_enricher_json_non_object_raises():
    with pytest.raises(ValueError, match="Enricher JSON payload is not an object"):
        enricher._parse_enricher_json(raw='["list"]')


def test_extract_message_content_valid():
    data = {"choices": [{"message": {"content": "hello"}}]}

    assert enricher._extract_message_content(data) == "hello"


def test_extract_message_content_no_choices():
    assert enricher._extract_message_content({}) is None


def test_extract_message_content_no_message():
    assert enricher._extract_message_content({"choices": [{}]}) is None


def test_extract_message_content_non_string_content():
    data = {"choices": [{"message": {"content": 123}}]}

    assert enricher._extract_message_content(data) is None
