"""Генерация песни через OpenRouter: SSE-поток, сборка base64-аудио, прогресс."""

import base64
import json
import logging
import math
import re
import time
from typing import Any, Protocol

import httpx

import config

log = logging.getLogger(__name__)


# Колбек прогресса: `on_progress(stage, fraction)`, fraction в диапазоне 0..1
class ProgressCallback(Protocol):
    async def __call__(self, stage: str, fraction: float) -> None: ...


# Типичное время генерации песни — на его основе оцениваем долю прогресса,
# т.к. поток SSE не сообщает общий размер ответа.
TYPICAL_GENERATION_SECONDS = 75.0

# Максимальный размер аудио в base64-символах (~30 МБ после декодирования).
# Защита от исчерпания памяти, если сервер шлёт аномально большой поток.
MAX_AUDIO_B64_LEN = 40 * 1024 * 1024


AUDIO_B64_RE = re.compile(r"data:audio/mpeg;base64,([A-Za-z0-9+/=]+)")


def _find_audio_b64(node: Any) -> str | None:
    """Рекурсивно ищет base64-аудио в JSON любой структуры.

    Структура ответа модели не зафиксирована контрактом, поэтому
    обходим узлы наугад, а не по заранее известным полям.

    Args:
        node: Узел JSON — строка, dict или list.

    Returns:
        Base64-строка аудио или None, если ничего не найдено.
    """
    if isinstance(node, str):
        match = AUDIO_B64_RE.search(node)
        if match:
            return match.group(1)
    elif isinstance(node, dict):
        for value in node.values():
            found = _find_audio_b64(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_audio_b64(item)
            if found:
                return found
    return None


def load_mock_audio() -> bytes:
    """Читает аудио из мок-файла, указанного в config.MOCK_FILE.

    Мок-файл — JSON, внутри которого рекурсивно ищется base64-строка
    с аудио в формате data-URI.

    Returns:
        Байты mp3-файла из мока.

    Raises:
        RuntimeError: Если аудио в мок-файле не найдено.
    """
    with open(config.MOCK_FILE, encoding="utf-8") as f:
        data = json.load(f)
    b64 = _find_audio_b64(data)
    if b64:
        return base64.b64decode(b64)
    raise RuntimeError("Аудио не найдено в мок-файле")


async def generate_song_real(prompt: str, on_progress: ProgressCallback | None = None) -> bytes:
    """Генерирует песню через OpenRouter, читая ответ как SSE-поток.

    Аудио приходит кусками в base64 внутри delta-чанков, поэтому
    собираем их в список и декодируем в конце.

    Args:
        prompt: Промпт для модели (описание песни / текст).
        on_progress: Опциональная корутина `on_progress(stage, fraction)`,
            вызывается по мере продвижения; fraction в диапазоне 0..1.

    Returns:
        Байты готового mp3-файла.

    Raises:
        RuntimeError: Если сервер вернул не-200, аудио не пришло в потоке,
            или поток превысил MAX_AUDIO_B64_LEN.
    """

    async def report(stage: str, fraction: float) -> None:
        if on_progress is None:
            return
        try:
            await on_progress(stage=stage, fraction=min(max(fraction, 0.0), 1.0))
        except Exception:
            log.exception("Ошибка в on_progress")

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
    total_b64 = 0
    started = time.monotonic()

    async def stream_progress() -> None:
        # Асимптота 1 - exp(-t/τ): даже если генерация затянется вдвое против
        # типичной, индикатор продолжает ползти, а не замирает на 95%
        elapsed = time.monotonic() - started
        fraction = 1.0 - math.exp(-elapsed / TYPICAL_GENERATION_SECONDS)
        await report(stage="Получаю аудио…", fraction=fraction * 0.95)

    await report(stage="Соединяюсь с сервером…", fraction=0.02)
    async with (
        httpx.AsyncClient(timeout=180.0) as client,
        client.stream(
            method="POST",
            url="https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp,
    ):
        if resp.status_code != 200:
            error_body = (await resp.aread()).decode("utf-8", "ignore")
            raise RuntimeError(f"OpenRouter {resp.status_code}: {error_body[:300]}")
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw_payload = line[6:].strip()
            if raw_payload == "[DONE]":
                break
            try:
                chunk = json.loads(raw_payload)
            except json.JSONDecodeError:
                continue
            # Пустой choices — легитимный случай для некоторых промежуточных чанков
            choices = chunk.get("choices") or [{}]
            delta = choices[0].get("delta", {})
            audio = delta.get("audio") or {}
            if audio.get("data"):
                audio_b64 = audio["data"]
                # Защита от кумулятивных чанков: если сервер шлёт не дельты,
                # а полные снимки аудио, каждый следующий чанк начинается
                # с предыдущего. Тогда заменяем, а не добавляем.
                if chunks and audio_b64.startswith(chunks[-1]):
                    total_b64 -= len(chunks[-1])
                    chunks.pop()
                total_b64 += len(audio_b64)
                if total_b64 > MAX_AUDIO_B64_LEN:
                    raise RuntimeError("Аудио в потоке превышает допустимый размер")
                chunks.append(audio_b64)
                await stream_progress()

    if not chunks:
        raise RuntimeError("Аудио не пришло в потоке")

    await report(stage="Собираю файл…", fraction=0.97)
    return base64.b64decode("".join(chunks))
