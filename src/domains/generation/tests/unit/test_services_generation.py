"""Юнит-тесты сервиса генерации: парсинг JSON и SSE-поток с мок-транспортом.

Сеть не трогаем: httpx.AsyncClient подменяется на заглушку,
поэтому тесты не зависят от OpenRouter и проходят офлайн.
"""

import base64
import json

import pytest

from core.config import settings
from domains.generation import service as gen

pytestmark = pytest.mark.unit


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _audio_chunk(data_b64: str, is_cumulative: bool = False, index: int | None = None) -> str:
    audio_obj: dict[str, object] = {"data": data_b64}
    if is_cumulative:
        audio_obj["is_cumulative"] = True
    if index is not None:
        audio_obj["index"] = index
    payload = {"choices": [{"delta": {"audio": audio_obj}}]}
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


def test_load_mock_audio_missing_file_raises(tmp_path, monkeypatch):
    """Несуществующий мок-файл даёт понятную ошибку, а не сырой FileNotFoundError."""
    monkeypatch.setattr(settings.generation, "MOCK_FILE", str(tmp_path / "missing.json"))

    with pytest.raises(RuntimeError, match="Cannot read mock file"):
        gen.load_mock_audio()


def test_load_mock_audio_invalid_json_raises(tmp_path, monkeypatch):
    """Битый JSON в мок-файле даёт понятную ошибку, а не сырой JSONDecodeError."""
    mock_file = tmp_path / "broken.json"
    mock_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(settings.generation, "MOCK_FILE", str(mock_file))

    with pytest.raises(RuntimeError, match="is not valid JSON"):
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
            [_audio_chunk(_b64(b"ABC")), _audio_chunk(_b64(b"DEF")), "data: [DONE]"],
            b"ABCDEF",
            id="delta-chunks",
        ),
        pytest.param(
            # Повторяющиеся одинаковые дельты корректно конкатенируются (AUD-010)
            [_audio_chunk(_b64(b"ABC")), _audio_chunk(_b64(b"ABC")), "data: [DONE]"],
            b"ABCABC",
            id="duplicate-deltas-preserve-all-data",
        ),
        pytest.param(
            # Вторая дельта начинается с префикса первой дельты — обе дельты сохраняются
            [_audio_chunk(_b64(b"ABC")), _audio_chunk(_b64(b"ABCDEF")), "data: [DONE]"],
            b"ABCABCDEF",
            id="deltas-with-shared-prefix-not-dropped",
        ),
        pytest.param(
            # Явный кумулятивный поток: снимки содержат предыдущие данные и дают приращение
            [
                _audio_chunk(_b64(b"ABC"), is_cumulative=True),
                _audio_chunk(_b64(b"ABCDEF"), is_cumulative=True),
                "data: [DONE]",
            ],
            b"ABCDEF",
            id="cumulative-chunks-yield-increment",
        ),
        pytest.param(
            # Явный кумулятивный поток с отслеживанием индекса
            [
                _audio_chunk(_b64(b"ABC"), is_cumulative=True, index=0),
                _audio_chunk(_b64(b"ABCDEF"), is_cumulative=True, index=len(_b64(b"ABC"))),
                "data: [DONE]",
            ],
            b"ABCDEF",
            id="cumulative-chunks-with-index",
        ),
        pytest.param(
            # Первый чанк обрезан посреди base64-группы (3 символа, не кратны 4):
            # декодировать его нельзя, буфер pending_b64 копится до следующего чанка
            [_audio_chunk(_b64(b"ABCDEF")[:3]), _audio_chunk(_b64(b"ABCDEF")[3:]), "data: [DONE]"],
            b"ABCDEF",
            id="incomplete-chunk-accumulates",
        ),
        pytest.param(
            # Буфер накапливается через два чанка подряд (по 2 символа),
            # валидная группа собирается только на третьем
            [
                _audio_chunk(_b64(b"ABCDEF")[:2]),
                _audio_chunk(_b64(b"ABCDEF")[2:4]),
                _audio_chunk(_b64(b"ABCDEF")[4:]),
                "data: [DONE]",
            ],
            b"ABCDEF",
            id="pending-spans-multiple-chunks",
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

    assert await gen.generate_song_real(prompt="спой про тестирование", gen_id=999) == expected


async def test_generate_song_real_reports_progress(patch_openrouter):
    events = []

    async def on_progress(stage: str, fraction: float) -> None:
        events.append((stage, fraction))

    patch_openrouter(FakeStreamResponse([_audio_chunk(_b64(b"ABC")), "data: [DONE]"]))
    await gen.generate_song_real(prompt="промпт", gen_id=999, on_progress=on_progress)

    stages = [stage for stage, _ in events]
    assert stages[0] == "Соединяюсь с сервером…"
    assert "Получаю аудио…" in stages
    assert stages[-1] == "Собираю файл…"
    assert all(0.0 <= fraction <= 1.0 for _, fraction in events)


@pytest.mark.parametrize(
    "response, match, exc_type",
    [
        pytest.param(FakeStreamResponse([], status_code=500), "OpenRouter 500", gen.GenerationAPIError, id="http-500"),
        pytest.param(
            FakeStreamResponse(["data: [DONE]"]),
            "No audio received",
            gen.GenerationAudioMissingError,
            id="stream-without-audio",
        ),
        pytest.param(
            FakeStreamResponse(["event: end"]),
            "terminal \\[DONE\\] event not received",
            gen.GenerationStreamError,
            id="empty-stream-without-done",
        ),
    ],
)
async def test_generate_song_real_failures(patch_openrouter, response, match, exc_type):
    patch_openrouter(response)

    with pytest.raises(exc_type, match=match):
        await gen.generate_song_real(prompt="промпт", gen_id=999)


async def test_generate_song_real_stream_without_done_raises(patch_openrouter):
    """Обрыв потока без терминального события [DONE] вызывает GenerationStreamError."""
    patch_openrouter(FakeStreamResponse([_audio_chunk(_b64(b"PARTIAL_AUDIO"))]))

    with pytest.raises(gen.GenerationStreamError, match="terminal \\[DONE\\] event not received"):
        await gen.generate_song_real(prompt="промпт", gen_id=999)


async def test_generate_song_real_oversized_sse_line_raises(patch_openrouter, monkeypatch):
    """Строка SSE, превышающая MAX_SSE_LINE_LENGTH, отклоняется до парсинга JSON."""
    monkeypatch.setattr(gen, "MAX_SSE_LINE_LENGTH", 32)
    oversized_line = "data: " + ("x" * 40)
    patch_openrouter(FakeStreamResponse([oversized_line, "data: [DONE]"]))

    with pytest.raises(gen.GenerationStreamError, match="SSE line exceeds limit"):
        await gen.generate_song_real(prompt="промпт", gen_id=999)


async def test_generate_song_real_rejects_oversized_audio(patch_openrouter, monkeypatch):
    # Лимит уменьшаем до 4 символов, чтобы не гонять через тест 40 МБ данных
    monkeypatch.setattr(gen, "MAX_AUDIO_B64_LEN", 4)
    patch_openrouter(FakeStreamResponse([_audio_chunk(_b64(b"longer-than-four-bytes"))]))

    with pytest.raises(RuntimeError, match="exceeds the allowed size"):
        await gen.generate_song_real(prompt="промпт", gen_id=999)


async def test_generate_song_real_swallows_progress_errors(patch_openrouter):
    """Сбой колбека прогресса не должен ронять генерацию."""

    async def broken_on_progress(stage: str, fraction: float) -> None:
        raise RuntimeError("progress blew up")

    patch_openrouter(FakeStreamResponse([_audio_chunk(_b64(b"ABC")), "data: [DONE]"]))

    assert await gen.generate_song_real(prompt="промпт", gen_id=999, on_progress=broken_on_progress) == b"ABC"


async def test_generate_song_real_retries_on_503(patch_openrouter, monkeypatch):
    """Retry-логика должна повторить запрос после 503 и успешно завершиться на третьей попытке."""
    attempts = []

    class RetryingFakeClient:
        """Заглушка с последовательностью ответов: 503 → 503 → 200."""

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def stream(self, *args, **kwargs):
            attempts.append(len(attempts) + 1)

            class _StreamContext:
                async def __aenter__(self):
                    if len(attempts) <= 2:
                        return FakeStreamResponse([], status_code=503)
                    return FakeStreamResponse([_audio_chunk(_b64(b"SUCCESS")), "data: [DONE]"], status_code=200)

                async def __aexit__(self, *exc_info):
                    return False

            return _StreamContext()

    monkeypatch.setattr(gen.httpx, "AsyncClient", RetryingFakeClient)

    result = await gen.generate_song_real(prompt="промпт", gen_id=999)

    assert result == b"SUCCESS"
    assert len(attempts) == 3, "Should retry exactly 3 times (2 failures + 1 success)"


async def test_generate_song_real_retries_on_timeout(patch_openrouter, monkeypatch):
    """Retry-логика должна повторить запрос после TimeoutException."""
    attempts = []

    class TimeoutFakeClient:
        """Заглушка с последовательностью: timeout → timeout → 200."""

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def stream(self, *args, **kwargs):
            attempts.append(len(attempts) + 1)

            class _StreamContext:
                async def __aenter__(self):
                    if len(attempts) <= 2:
                        raise gen.httpx.TimeoutException("Connection timeout")
                    return FakeStreamResponse([_audio_chunk(_b64(b"RECOVERED")), "data: [DONE]"], status_code=200)

                async def __aexit__(self, *exc_info):
                    return False

            return _StreamContext()

    monkeypatch.setattr(gen.httpx, "AsyncClient", TimeoutFakeClient)

    result = await gen.generate_song_real(prompt="промпт", gen_id=999)

    assert result == b"RECOVERED"
    assert len(attempts) == 3


async def test_generate_song_real_fails_after_max_retries(patch_openrouter, monkeypatch):
    """После исчерпания попыток retry должен пробросить исключение."""
    attempts = []

    class AlwaysFailingClient:
        """Заглушка, которая всегда возвращает 503."""

        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def stream(self, *args, **kwargs):
            attempts.append(len(attempts) + 1)

            class _StreamContext:
                async def __aenter__(self):
                    return FakeStreamResponse([], status_code=503)

                async def __aexit__(self, *exc_info):
                    return False

            return _StreamContext()

    monkeypatch.setattr(gen.httpx, "AsyncClient", AlwaysFailingClient)

    with pytest.raises(gen.GenerationAPIError, match="OpenRouter 503"):
        await gen.generate_song_real(prompt="промпт", gen_id=999)

    assert len(attempts) == 3, "Should attempt exactly 3 times before giving up"


async def test_generate_song_real_no_retry_on_400(patch_openrouter):
    """Ошибки 400 не должны повторяться — это ошибка клиента, а не сервера."""
    patch_openrouter(FakeStreamResponse([], status_code=400))

    with pytest.raises(gen.GenerationAPIError, match="OpenRouter 400"):
        await gen.generate_song_real(prompt="промпт", gen_id=999)


async def test_generate_song_real_stream_timeout_retries_and_notifies_ux(monkeypatch):
    """AUD-040: Сбой или таймаут во время чтения SSE-потока ретраится и вызывает on_retry."""
    stream_calls = 0
    retry_notifications = []

    class FailingStreamResponse(FakeStreamResponse):
        async def aiter_lines(self):
            yield _audio_chunk(_b64(b"FIRST_PART"))
            raise gen.httpx.ReadTimeout("Stream read timed out")

    class RetryingStreamClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def stream(self, *args, **kwargs):
            nonlocal stream_calls
            stream_calls += 1

            class _StreamContext:
                async def __aenter__(self):
                    if stream_calls < 3:
                        return FailingStreamResponse([])
                    return FakeStreamResponse([_audio_chunk(_b64(b"RECOVERED")), "data: [DONE]"])

                async def __aexit__(self, *exc_info):
                    return False

            return _StreamContext()

    async def fake_on_retry(attempt: int, max_attempts: int, exc: BaseException | None) -> None:
        retry_notifications.append((attempt, max_attempts, type(exc)))

    monkeypatch.setattr(gen.httpx, "AsyncClient", RetryingStreamClient)

    result = await gen.generate_song_real(prompt="промпт", gen_id=999, on_retry=fake_on_retry)

    assert result == b"RECOVERED"
    assert stream_calls == 3
    assert len(retry_notifications) == 2
    assert retry_notifications[0][0] == 1
    assert retry_notifications[0][1] == 3
    assert issubclass(retry_notifications[0][2], gen.GenerationStreamError)
    assert retry_notifications[1][0] == 2
    assert retry_notifications[1][1] == 3


async def test_generate_song_real_stream_failure_exhausts_retries_and_raises(monkeypatch):
    """AUD-040: Окончательное падение всех ретраев при сбое стрима выбрасывает ошибку."""
    stream_calls = 0
    retry_notifications = []

    class AlwaysFailingStreamResponse(FakeStreamResponse):
        async def aiter_lines(self):
            yield _audio_chunk(_b64(b"PARTIAL"))
            raise gen.httpx.ReadTimeout("Stream read timed out")

    class AlwaysFailingClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def stream(self, *args, **kwargs):
            nonlocal stream_calls
            stream_calls += 1

            class _StreamContext:
                async def __aenter__(self):
                    return AlwaysFailingStreamResponse([])

                async def __aexit__(self, *exc_info):
                    return False

            return _StreamContext()

    async def fake_on_retry(attempt: int, max_attempts: int, exc: BaseException | None) -> None:
        retry_notifications.append(attempt)

    monkeypatch.setattr(gen.httpx, "AsyncClient", AlwaysFailingClient)

    with pytest.raises(gen.GenerationStreamError, match="Stream interrupted during reading"):
        await gen.generate_song_real(prompt="промпт", gen_id=999, on_retry=fake_on_retry)

    assert stream_calls == 3
    assert retry_notifications == [1, 2]
