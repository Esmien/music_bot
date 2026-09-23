"""Генерация песни через OpenRouter: SSE-поток, сборка base64-аудио, прогресс."""

import base64
import io
import json
import logging
import math
import re
import time
from typing import Any, Protocol

import httpx

from core.config import settings
from core.utils.exceptions import (
    GenerationAPIError,
    GenerationAudioMissingError,
    GenerationConfigurationError,
    GenerationFileError,
    GenerationStreamError,
)
from core.utils.stream_parser import _parse_openrouter_sse

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

# Ожидаемый формат аудио
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
    # Base case - если нашли строку, прерываем рекурсию
    if isinstance(node, str):
        match = AUDIO_B64_RE.search(node)
        if match:
            return match.group(1)
    # Ищем ожидаемую строку в значения словаря
    elif isinstance(node, dict):
        for value in node.values():
            found = _find_audio_b64(value)
            if found:
                return found
    # Ищем ожидаемую строку в списке
    elif isinstance(node, list):
        for item in node:
            found = _find_audio_b64(item)
            if found:
                return found
    return None


def load_mock_audio() -> bytes:
    """Читает аудио из мок-файла, указанного в config.MOCK_FILE.

    Мок-файл — JSON, внутри которого рекурсивно ищется base64-строка
    с аудио в формате data-URI. Мок нужен только для отладки, поэтому
    проблемы с ним не проверяются на старте, а отдаются вызывающей
    стороне как понятная ошибка в рантайме.

    Returns:
        Байты mp3-файла из мока.

    Raises:
        GenerationConfigurationError: Если MOCK_MODE включён, но MOCK_FILE не задан.
        GenerationFileError: Если mock-файл не читается или не является JSON.
        GenerationAudioMissingError: Если аудио в mock-файле не найдено.
    """
    if not settings.generation.MOCK_FILE:
        raise GenerationConfigurationError("MOCK_MODE is enabled but MOCK_FILE is not set")
    try:
        with open(settings.generation.MOCK_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as error:
        raise GenerationFileError(f"Cannot read mock file {settings.generation.MOCK_FILE!r}") from error
    except json.JSONDecodeError as error:
        raise GenerationFileError(f"Mock file {settings.generation.MOCK_FILE!r} is not valid JSON") from error
    b64 = _find_audio_b64(data)
    if b64:
        return base64.b64decode(b64)
    raise GenerationAudioMissingError("Audio not found in mock file")


async def generate_song_real(prompt: str, on_progress: ProgressCallback | None = None) -> bytes:
    """Генерирует песню через OpenRouter, читая ответ как SSE-поток.

    Аудио приходит кусками в base64 внутри delta-чанков. Чтобы не держать
    в памяти весь поток целиком, декодируем base64 порциями по мере
    поступления чанков и пишем готовые байты в io.BytesIO: в памяти
    остаются только недекодированный «хвост» (< 4 символа) и само аудио.

    Args:
        prompt: Промпт для модели (описание песни / текст).
        on_progress: Опциональная корутина `on_progress(stage, fraction)`,
            вызывается по мере продвижения; fraction в диапазоне 0..1.

    Returns:
        Байты готового mp3-файла.

    Raises:
        GenerationAPIError: Если сервер вернул не-200.
        GenerationStreamError: Если поток превысил MAX_AUDIO_B64_LEN.
        GenerationAudioMissingError: Если аудио не пришло в потоке.
    """

    async def report(stage: str, fraction: float) -> None:
        if on_progress is None:
            return
        try:
            await on_progress(stage=stage, fraction=min(max(fraction, 0.0), 1.0))
        except Exception:
            log.exception("Error in on_progress")

    headers = {
        "Authorization": f"Bearer {settings.bot.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me",
        "X-Title": "Lyria TG Bot",
    }
    payload = {
        "model": settings.bot.MODEL_ID,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        "stream": True,
        "modalities": ["text", "audio"],
        "audio": {"format": "mp3"},
    }

    # Готовые байты аудио
    decoded_audio = io.BytesIO()
    # Недекодированный хвост base64 (ждёт дополнения до группы из 4 символов)
    pending_b64 = ""
    # Последний сырой чанк — нужен для детекта кумулятивных снимков
    last_chunk = ""
    # Счетчик размера файла
    total_b64 = 0
    # Точка отсчета таймера для прогресс-бара
    started = time.monotonic()

    # Рисуем заглушку на старте генерации
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
            raise GenerationAPIError(f"OpenRouter {resp.status_code}: {error_body[:300]}")

        # Читаем очищенный поток из парсера
        async for raw_chunk in _parse_openrouter_sse(resp):
            if last_chunk and raw_chunk.startswith(last_chunk):
                # Сервер шлёт снимки, а не дельты: новый чанк содержит
                # предыдущий. В decoded_audio уже лежат декодированные байты
                # префикса, поэтому декодируем только приращение.
                pending_b64 += raw_chunk[len(last_chunk) :]
                total_b64 += len(raw_chunk) - len(last_chunk)
            else:
                pending_b64 += raw_chunk
                total_b64 += len(raw_chunk)

            if total_b64 > MAX_AUDIO_B64_LEN:
                raise GenerationStreamError("Audio in stream exceeds the allowed size")

            # base64 декодируется группами по 4 символа: готовую часть
            # сразу пишем в BytesIO, хвост ждёт следующих чанков
            aligned_len = len(pending_b64) - len(pending_b64) % 4
            if aligned_len:
                decoded_audio.write(base64.b64decode(pending_b64[:aligned_len]))
                pending_b64 = pending_b64[aligned_len:]

            last_chunk = raw_chunk

            # Обновляем UI асимптотически от времени
            elapsed = time.monotonic() - started
            fraction = 1.0 - math.exp(-elapsed / TYPICAL_GENERATION_SECONDS)
            await report(stage="Получаю аудио…", fraction=fraction * 0.95)

    if not last_chunk:
        raise GenerationAudioMissingError("No audio received in stream")

    await report(stage="Собираю файл…", fraction=0.97)
    if pending_b64:
        decoded_audio.write(base64.b64decode(pending_b64))
    return decoded_audio.getvalue()
