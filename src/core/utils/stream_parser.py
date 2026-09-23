import json
from collections.abc import AsyncGenerator

import httpx


async def _parse_openrouter_sse(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Читает SSE-поток и отдаёт base64-строки аудио по мере их поступления."""
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue

        # После "data:" может не быть пробела или их может быть несколько —
        # отрезаем префикс до первого двоеточия и чистим пробелы
        raw_payload = line.split(":", 1)[1].strip()
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
