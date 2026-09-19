import json
from collections.abc import AsyncGenerator

import httpx


async def _parse_openrouter_sse(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Читает SSE-поток и отдаёт base64-строки аудио по мере их поступления."""
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue

        raw_payload = line[6:].strip()
        if raw_payload == "[DONE]":
            break

        try:
            chunk = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue

        # Извлекаем аудио из глубоко вложенной структуры дельты
        choices = chunk.get("choices") or [{}]
        delta = choices[0].get("delta", {})
        audio = delta.get("audio") or {}

        if audio.get("data"):
            yield audio["data"]
