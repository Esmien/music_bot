"""Юнит-тесты сервиса генерации: парсинг JSON и SSE-поток с мок-транспортом.

Сеть не трогаем: httpx.AsyncClient подменяется на заглушку,
поэтому тесты не зависят от OpenRouter и проходят офлайн.
"""

import base64
import json

import pytest

from core.config import settings
from services import generation as gen

pytestmark = pytest.mark.unit


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _audio_chunk(data_b64: str) -> str:
    payload = {"choices": [{"delta": {"audio": {"data": data_b64}}}]}
    return "data: " + json.dumps(payload)


# --- _find_audio_b64 ---


@pytest.mark.parametrize(
    "node, expected",
    [
        pytest.param("data:audio/mpeg;base64,QUJD", "QUJD", id="plain-string"),
        pytest.param({"deep": {"x": "data:audio/mpeg;base64,QUJD"}}, "QUJD", id="nested-dict"),
        pytest.param(["noise", ["data:audio/mpeg;base64,QUJD"]], "QUJD", id="nested-list"),
        pytest.param("data:audio/wav;base64,QUJD", None, id="wrong-mime"),
        pytest.param("без аудио", None, id="no-match"),
        pytest.param({}, None, id="empty-dict"),
        pytest.param(None, None, id="not-a-container"),
    ],
)
def test_find_audio_b64(node, expected):
    assert gen._find_audio_b64(node) == expected


# --- load_mock_audio ---


def test_load_mock_audio_reads_base64(tmp_path, monkeypatch):
    raw = b"mock-mp3-bytes"
    mock_file = tmp_path / "mock.json"
    mock_file.write_text(json.dumps({"outer": {"audio": f"data:audio/mpeg;base64,{_b64(raw)}"}}))
    monkeypatch.setattr(settings.generation, "MOCK_FILE", str(mock_file))

    assert gen.load_mock_audio() == raw


def test_load_mock_audio_without_audio_raises(tmp_path, monkeypatch):
    mock_file = tmp_path / "mock.json"
    mock_file.write_text(json.dumps({"nothing": "here"}))
    monkeypatch.setattr(settings.generation, "MOCK_FILE", str(mock_file))

    with pytest.raises(RuntimeError, match="not found"):
        gen.load_mock_audio()


def test_load_mock_audio_without_file_raises(monkeypatch):
    """Пустой MOCK_FILE даёт понятную ошибку, а не FileNotFoundError."""
    monkeypatch.setattr(settings.generation, "MOCK_FILE", "")

    with pytest.raises(RuntimeError, match="MOCK_FILE is not set"):
        gen.load_mock_audio()


# --- generate_song_real ---


class FakeStreamResponse:
    """Заглушка httpx.Response для потокового чтения SSE."""

    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return f"error body {self.status_code}".encode()


class FakeAsyncClient:
    """Заглушка httpx.AsyncClient: stream() отдаёт фиксированный ответ."""

    def __init__(self, response, **kwargs):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def stream(self, *args, **kwargs):
        response = self._response

        class _StreamContext:
            async def __aenter__(self):
                return response

            async def __aexit__(self, *exc_info):
                return False

        return _StreamContext()


@pytest.fixture
def patch_openrouter(monkeypatch):
    """Подменяет httpx.AsyncClient в сервисе генерации на заглушку."""

    def _install(response):
        monkeypatch.setattr(gen.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(response))

    return _install


@pytest.mark.parametrize(
    "lines, expected",
    [
        pytest.param(
            [_audio_chunk(_b64(b"ABC")), _audio_chunk(_b64(b"DEF"))],
            b"ABCDEF",
            id="delta-chunks",
        ),
        pytest.param(
            # Второй чанк начинается с первого: сервер шлёт снимки, а не дельты
            [_audio_chunk(_b64(b"ABC")), _audio_chunk(_b64(b"ABCDEF"))],
            b"ABCDEF",
            id="cumulative-chunks-are-replaced",
        ),
        pytest.param(
            [
                "event: ping",
                "data: not-json",
                _audio_chunk(_b64(b"XYZ")),
                "data: [DONE]",
                _audio_chunk(_b64(b"AFTER-DONE")),
            ],
            b"XYZ",
            id="junk-skipped-and-done-stops-stream",
        ),
    ],
)
async def test_generate_song_real_assembles_audio(patch_openrouter, lines, expected):
    patch_openrouter(FakeStreamResponse(lines))

    assert await gen.generate_song_real("спой про тестирование") == expected


async def test_generate_song_real_reports_progress(patch_openrouter):
    events = []

    async def on_progress(stage: str, fraction: float) -> None:
        events.append((stage, fraction))

    patch_openrouter(FakeStreamResponse([_audio_chunk(_b64(b"ABC")), "data: [DONE]"]))
    await gen.generate_song_real("промпт", on_progress)

    stages = [stage for stage, _ in events]
    assert stages[0] == "Соединяюсь с сервером…"
    assert "Получаю аудио…" in stages
    assert stages[-1] == "Собираю файл…"
    assert all(0.0 <= fraction <= 1.0 for _, fraction in events)


@pytest.mark.parametrize(
    "response, match",
    [
        pytest.param(FakeStreamResponse([], status_code=500), "OpenRouter 500", id="http-500"),
        pytest.param(FakeStreamResponse(["data: [DONE]"]), "No audio received", id="stream-without-audio"),
        pytest.param(FakeStreamResponse(["event: end"]), "No audio received", id="empty-stream"),
    ],
)
async def test_generate_song_real_failures(patch_openrouter, response, match):
    patch_openrouter(response)

    with pytest.raises(RuntimeError, match=match):
        await gen.generate_song_real("промпт")


async def test_generate_song_real_rejects_oversized_audio(patch_openrouter, monkeypatch):
    # Лимит уменьшаем до 4 символов, чтобы не гонять через тест 40 МБ данных
    monkeypatch.setattr(gen, "MAX_AUDIO_B64_LEN", 4)
    patch_openrouter(FakeStreamResponse([_audio_chunk(_b64(b"longer-than-four-bytes"))]))

    with pytest.raises(RuntimeError, match="exceeds the allowed size"):
        await gen.generate_song_real("промпт")


async def test_generate_song_real_swallows_progress_errors(patch_openrouter):
    """Сбой колбека прогресса не должен ронять генерацию."""

    async def broken_on_progress(stage: str, fraction: float) -> None:
        raise RuntimeError("progress blew up")

    patch_openrouter(FakeStreamResponse([_audio_chunk(_b64(b"ABC")), "data: [DONE]"]))

    assert await gen.generate_song_real("промпт", broken_on_progress) == b"ABC"
