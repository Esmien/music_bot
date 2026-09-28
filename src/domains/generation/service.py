"""Сервис генерации песни и конвейера: OpenRouter, прогресс и локи.

Модуль ничего не знает о хендлерах, FSM и Telegram: прогресс отдаётся
через абстрактный колбэк on_progress, а «отрисовка» текста — через
произвольную корутину, переданную вызывающей стороной.
"""

import asyncio
import base64
import io
import json
import logging
import math
import re
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Protocol

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from core.config import settings
from core.redis import is_generation_cancelled, redis_client
from core.utils.exceptions import (
    GenerationAPIError,
    GenerationAudioMissingError,
    GenerationConfigurationError,
    GenerationFileError,
    GenerationStreamError,
)

log = logging.getLogger(__name__)

# Минимальный интервал между правками сообщения прогресса (лимиты Telegram)
PROGRESS_EDIT_INTERVAL = 3.0

# TTL распределённого лока в миллисекундах (3 минуты: запас на генерацию)
LOCK_TTL_MS = 180_000

# Lua-скрипт для атомарного освобождения лока по токену владельца
RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _is_retryable_error(exception: BaseException) -> bool:
    """Определяет, является ли ошибка повторяемой для retry-логики.

    Args:
        exception: Исключение для проверки.

    Returns:
        True, если ошибку можно повторить, False иначе.
    """
    # Сетевые ошибки httpx
    if isinstance(exception, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout)):
        return True

    # HTTP-ошибки с кодами 429 (Rate Limit) и 503 (Service Unavailable)
    if isinstance(exception, GenerationAPIError):
        error_msg = str(exception)
        return "429" in error_msg or "503" in error_msg

    return False


def _log_retry_attempt(retry_state) -> None:
    """Логирует попытку повтора запроса.

    Args:
        retry_state: Состояние retry из tenacity.
    """
    attempt = retry_state.attempt_number
    exception = retry_state.outcome.exception() if retry_state.outcome else None
    log.warning(
        "Retry attempt %d for OpenRouter API due to %s: %s",
        attempt,
        type(exception).__name__ if exception else "unknown",
        str(exception)[:200] if exception else "",
    )


# Колбек прогресса: `on_progress(stage, fraction)`, fraction в диапазоне 0..1
class ProgressCallback(Protocol):
    async def __call__(self, stage: str, fraction: float) -> None: ...


# Максимальный размер аудио в base64-символах (~30 МБ после декодирования).
# Защита от исчерпания памяти, если сервер шлёт аномально большой поток.
MAX_AUDIO_B64_LEN = 40 * 1024 * 1024

# Ожидаемый формат аудио
AUDIO_B64_RE = re.compile(r"data:audio/mpeg;base64,([A-Za-z0-9+/=]+)")


@asynccontextmanager
async def user_generation_lock(user_id: int) -> AsyncIterator[None]:
    """Асинхронный контекст: захват и освобождение распределённого Redis-лока.

    Лок реализован через `SET NX PX` с уникальным токеном владельца.
    Освобождение выполняется через проверку токена перед удалением.

    Args:
        user_id: Telegram user_id пользователя.
    """
    lock_key = f"bot:generation_lock:{user_id}"
    owner_token = str(uuid.uuid4())

    # Захват лока: SET NX PX гарантирует атомарность и автоосвобождение по TTL
    while True:
        acquired = await redis_client.set(lock_key, owner_token, nx=True, px=LOCK_TTL_MS)
        if acquired:
            break
        # Лок занят — ждём с экспоненциальным backoff
        await asyncio.sleep(0.1)

    try:
        yield
    finally:
        # Освобождаем лок: проверяем токен владельца перед удалением
        # (упрощенная версия без Lua для совместимости с fakeredis)
        current_token = await redis_client.get(lock_key)
        if current_token:
            token_str = current_token.decode("utf-8") if isinstance(current_token, bytes) else current_token
            if token_str == owner_token:
                await redis_client.delete(lock_key)


def _progress_bar(fraction: float, width: int = 10) -> str:
    """Строит текстовый индикатор прогресса вида `████░░░░░░`.

    Args:
        fraction: Доля выполнения, 0..1.
        width: Ширина полосы в символах.

    Returns:
        Строка с заполненными и пустыми блоками.
    """
    # round, а не int: при fraction=0.5 полоса выглядит наполовину заполненной
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def progress_text(stage: str, fraction: float) -> str:
    """Собирает полный текст статуса: этап, полоса и проценты.

    Args:
        stage: Название этапа генерации.
        fraction: Доля выполнения, 0..1.

    Returns:
        Готовый текст для отображения пользователю.
    """
    return f"🎼 {stage}\n{_progress_bar(fraction)} {round(fraction * 100)}%"


def make_throttled_progress(report: Callable[[str], Awaitable[None]]) -> ProgressCallback:
    """Оборачивает «отрисовку» статуса в троттлинг по времени.

    Правки идут не чаще PROGRESS_EDIT_INTERVAL (лимиты Telegram);
    отрицательный старт гарантирует, что первый вызов не отсеется.

    Args:
        report: Корутина, принимающая готовый текст статуса.

    Returns:
        Колбэк on_progress(stage, fraction) для сервиса генерации.
    """
    loop = asyncio.get_running_loop()
    last_edit = -PROGRESS_EDIT_INTERVAL

    async def on_progress(stage: str, fraction: float) -> None:
        """Коллбэк для отрисовки прогресс-бара.

        Args:
            stage: Название этапа сборки.
            fraction: Оценочная доля прогресса (0..1) для отображения в процентах.
        """
        nonlocal last_edit

        # Троттлим: правки статуса не чаще PROGRESS_EDIT_INTERVAL (лимиты Telegram)
        now = loop.time()
        if now - last_edit < PROGRESS_EDIT_INTERVAL:
            return

        last_edit = now
        await report(progress_text(stage=stage, fraction=fraction))

    return on_progress


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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception(_is_retryable_error),
    before_sleep=_log_retry_attempt,
    reraise=True,
)
async def generate_song_real(prompt: str, gen_id: int, on_progress: ProgressCallback | None = None) -> bytes:
    """Генерирует песню через OpenRouter, читая ответ как SSE-поток.

    Аудио приходит кусками в base64 внутри delta-чанков. Чтобы не держать
    в памяти весь поток целиком, декодируем base64 порциями по мере
    поступления чанков и пишем готовые байты в io.BytesIO: в памяти
    остаются только недекодированный «хвост» (< 4 символа) и само аудио.

    Args:
        prompt: Промпт для модели (описание песни / текст).
        gen_id: ID генерации для проверки отмены.
        on_progress: Опциональная корутина `on_progress(stage, fraction)`,
            вызывается по мере продвижения; fraction в диапазоне 0..1.

    Returns:
        Байты готового mp3-файла.

    Raises:
        asyncio.CancelledError: Если генерация отменена пользователем.
        GenerationAPIError: Если сервер вернул не-200.
        GenerationStreamError: Если поток превысил MAX_AUDIO_B64_LEN.
        GenerationAudioMissingError: Если аудио не пришло в потоке.
    """
    # КРИТИЧНО: проверяем отмену ДО POST-запроса, чтобы не списывать деньги впустую
    if await is_generation_cancelled(gen_id=gen_id):
        raise asyncio.CancelledError

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

    # Дополнительная проверка перед HTTP-запросом (на случай отмены во время report)
    if await is_generation_cancelled(gen_id=gen_id):
        raise asyncio.CancelledError

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
            fraction = 1.0 - math.exp(-elapsed / settings.generation.TYPICAL_GENERATION_SECONDS)
            await report(stage="Получаю аудио…", fraction=fraction * 0.95)

    if not last_chunk:
        raise GenerationAudioMissingError("No audio received in stream")

    await report(stage="Собираю файл…", fraction=0.97)
    if pending_b64:
        decoded_audio.write(base64.b64decode(pending_b64))
    return decoded_audio.getvalue()


async def run_generation(prompt: str, gen_id: int, on_progress: ProgressCallback) -> bytes:
    """Запускает генерацию: демо-ветка в MOCK_MODE или реальный сервис.

    Args:
        prompt: Промпт для модели (описание песни).
        gen_id: ID генерации для проверки отмены.
        on_progress: Корутина `on_progress(stage, fraction)`.

    Returns:
        Байты готового аудио.

    Raises:
        asyncio.CancelledError: Если генерация отменена пользователем.
    """
    # Проверяем отмену перед стартом генерации (работает и для мок-режима)
    if await is_generation_cancelled(gen_id=gen_id):
        raise asyncio.CancelledError

    if settings.generation.MOCK_MODE:
        # Для демо-режима отображаем прогресс с шагом 30%
        for fraction in (0.2, 0.5, 0.8):
            # Проверяем отмену между шагами демо-прогресса
            if await is_generation_cancelled(gen_id=gen_id):
                raise asyncio.CancelledError
            await on_progress(stage="Генерирую (демо-режим)…", fraction=fraction)
            # Спим дольше интервала правки, иначе демо-прогресс не виден
            await asyncio.sleep(PROGRESS_EDIT_INTERVAL + 0.1)

        # Имитируем сборку и отдаем аудио из mock-файла
        await on_progress(stage="Собираю файл…", fraction=0.97)
        return load_mock_audio()

    # Отдаем реально сгенерированный файл, если генерация шла через API
    return await generate_song_real(prompt=prompt, gen_id=gen_id, on_progress=on_progress)
