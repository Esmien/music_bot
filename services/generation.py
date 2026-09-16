import base64
import json
import logging
import re

import httpx

import config

log = logging.getLogger(__name__)


def load_mock_audio() -> bytes:
    with open(config.MOCK_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for msg in data[0]["chat"]["history"]["messages"].values():
        content = msg.get("content", "")
        m = re.search(r'src="data:audio/mpeg;base64,([^"]+)"', content)
        if m:
            return base64.b64decode(m.group(1))
    raise RuntimeError("Аудио не найдено в мок-файле")


async def generate_song_real(prompt: str) -> bytes:
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me",
        "X-Title": "Lyria TG Bot",
    }
    payload = {
        "model": config.MODEL_ID,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "stream": True,
        "modalities": ["text", "audio"],
        "audio": {"format": "mp3"},
    }

    chunks: list[str] = []
    async with (
        httpx.AsyncClient(timeout=180.0) as client,
        client.stream(
            "POST",
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp,
    ):
        if resp.status_code != 200:
            err = (await resp.aread()).decode("utf-8", "ignore")
            raise RuntimeError(f"OpenRouter {resp.status_code}: {err[:300]}")
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            s = line[6:].strip()
            if s == "[DONE]":
                break
            try:
                chunk = json.loads(s)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            audio = delta.get("audio") or {}
            if audio.get("data"):
                chunks.append(audio["data"])

    if not chunks:
        raise RuntimeError("Аудио не пришло в потоке")
    return base64.b64decode("".join(chunks))
