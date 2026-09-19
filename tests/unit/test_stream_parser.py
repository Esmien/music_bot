import pytest

from utils.stream_parser import _parse_openrouter_sse


def _audio_chunk(data_b64: str) -> str:
    """Формирует SSE-строку с аудио-дельтой, как их шлёт OpenRouter."""

    return f'data: {{"choices": [{{"delta": {{"audio": {{"data": "{data_b64}"}}}}}}]}}'


class FakeStreamResponse:
    """Заглушка httpx.Response для потокового чтения SSE."""

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.parametrize(
    "lines, expected",
    [
        pytest.param(
            [_audio_chunk("QUJD"), _audio_chunk("REVG")],
            ["QUJD", "REVG"],
            id="multiple-audio-chunks",
        ),
        pytest.param(
            ["event: end", _audio_chunk("QUJD"), "data: [DONE]"],
            ["QUJD"],
            id="skips-non-data-lines",
        ),
        pytest.param(
            [_audio_chunk("QUJD"), "data: [DONE]", _audio_chunk("REVG")],
            ["QUJD"],
            id="stops-on-done",
        ),
        pytest.param(["data: {broken json"], [], id="skips-invalid-json"),
        pytest.param(['data: {"choices": []}'], [], id="empty-choices"),
        pytest.param(
            ['data: {"choices": [{"delta": {"audio": {}}}]}'],
            [],
            id="audio-without-data",
        ),
        pytest.param(
            ['data: {"choices": [{"delta": {}}]}'],
            [],
            id="delta-without-audio",
        ),
        pytest.param(["data: [DONE]"], [], id="done-without-audio"),
        pytest.param([], [], id="empty-stream"),
    ],
)
async def test_parse_openrouter_sse(lines, expected):
    """Парсер отдаёт только base64-аудио до [DONE], пропуская мусор."""

    result = [chunk async for chunk in _parse_openrouter_sse(FakeStreamResponse(lines))]

    assert result == expected
