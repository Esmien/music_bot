"""Сервис генерации песни и конвейера: OpenRouter, прогресс и локи.

Модуль ничего не знает о хендлерах, FSM и Telegram: прогресс отдаётся
через абстрактный колбэк on_progress, а «отрисовка» текста — через
произвольную корутину, переданную вызывающей стороной.
"""

import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import re
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import select
from tenacity import retry

from core.config import settings
from core.database.engine import get_session
from core.redis import RELEASE_LOCK_SCRIPT, is_generation_cancelled, redis_client
from core.types import JSONValue, ProgressCallback, ProgressReporter
from core.utils.exceptions import (
    GenerationAPIError,
    GenerationAudioMissingError,
    GenerationConfigurationError,
    GenerationFileError,
    GenerationLockTimeoutError,
    GenerationStreamError,
)
from core.utils.retry import (
    DEFAULT_RETRY_STOP,
    default_retry_predicate,
    default_retry_wait,
    make_retry_logger,
)
from domains.generation.models import Generation, GenerationStatus

log = logging.getLogger(__name__)

# Минимальный интервал между правками сообщения прогресса (лимиты Telegram)
PROGRESS_EDIT_INTERVAL = 3.0

# TTL распределённого лока в миллисекундах (3 минуты: запас на генерацию)
LOCK_TTL_MS = 180_000

_log_generation_retry = make_retry_logger("OpenRouter Generation API")


# Максимальный размер отдельной строки SSE (1 МБ) для защиты от OOM до парсинга JSON
MAX_SSE_LINE_LENGTH = 1024 * 1024

# Максимальный размер аудио в base64-символах (~30 МБ после декодирования).
# Защита от исчерпания памяти, если сервер шлёт аномально большой поток.
MAX_AUDIO_B64_LEN = 40 * 1024 * 1024

# Ожидаемый формат аудио
AUDIO_B64_RE = re.compile(r"data:audio/mpeg;base64,([A-Za-z0-9+/=]+)")


@dataclass(frozen=True)
class AudioChunk:
    """Элемент аудиопотока SSE.

    Attributes:
        data: Base64-данные аудио.
        index: Опциональный индекс смещения в кумулятивном потоке.
        is_cumulative: Флаг кумулятивного кадра (снимок вместо дельты).
    """

    data: str
    index: int | None = None
    is_cumulative: bool = False


@asynccontextmanager
async def user_generation_lock(
    user_id: int,
    *,
    timeout: float = 10.0,
    retry_interval: float = 0.05,
) -> AsyncIterator[None]:
    """Асинхронный контекст: захват и освобождение распределённого Redis-лока.

    Лок реализован через `SET NX PX` с уникальным токеном владельца.
    Освобождение выполняется строго через атомарный Lua-скрипт (compare-and-delete).
    Ожидание захвата ограничено таймаутом (bounded wait).

    Args:
        user_id: Telegram user_id пользователя.
        timeout: Таймаут ожидания захвата блокировки в секундах.
        retry_interval: Интервал опроса Redis при ожидании блокировки в секундах.

    Yields:
        None.

    Raises:
        GenerationLockTimeoutError: Если лок не удалось захватить за отведённый таймаут.
    """
    lock_key = f"bot:generation_lock:{user_id}"
    owner_token = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    # Захват лока: SET NX PX гарантирует атомарность и автоосвобождение по TTL
    while True:
        acquired = await redis_client.set(name=lock_key, value=owner_token, nx=True, px=LOCK_TTL_MS)
        if acquired:
            break

        remaining = deadline - loop.time()
        if remaining <= 0:
            raise GenerationLockTimeoutError(f"Failed to acquire generation lock for user {user_id} within {timeout}s")

        await asyncio.sleep(min(retry_interval, remaining))

    try:
        yield
    finally:
        # Атомарное освобождение лока: удаляем только если значение совпадает с токеном владельца
        try:
            await redis_client.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, owner_token)
        except Exception as e:
            # DEVIATION: Fallback для тестового окружения с fakeredis без библиотеки lupa
            if "unknown command 'eval'" in str(e).lower():
                current_val = await redis_client.get(lock_key)
                if isinstance(current_val, bytes):
                    current_val = current_val.decode("utf-8")
                if current_val == owner_token:
                    await redis_client.delete(lock_key)
            else:
                log.error("Failed to release Redis lock for user %s: %s", user_id, e)
                raise


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


def make_throttled_progress(report: ProgressReporter) -> ProgressCallback:
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


def _find_audio_b64(node: JSONValue) -> str | None:
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


async def _parse_openrouter_sse(response: httpx.Response) -> AsyncGenerator[AudioChunk, None]:
    """Читает SSE-поток и отдаёт порции base64-аудио.

    Проверяет размер каждой строки до разбора JSON во избежание DoS/OOM
    и контролирует получение терминального события [DONE].

    Args:
        response: HTTP-ответ от OpenRouter со стримом.

    Yields:
        AudioChunk с base64-данными и признаками формата потока.

    Raises:
        GenerationStreamError: Если превышен лимит длины строки или стрим оборвался без [DONE].
    """
    received_done = False

    async for line in response.aiter_lines():
        if len(line) > MAX_SSE_LINE_LENGTH:
            raise GenerationStreamError(f"SSE line exceeds limit of {MAX_SSE_LINE_LENGTH} characters")

        if not line.startswith("data:"):
            continue

        # После "data:" может не быть пробела или их может быть несколько —
        # отрезаем префикс до первого двоеточия и чистим пробелы
        raw_payload = line.split(":", 1)[1].strip()
        if raw_payload == "[DONE]":
            received_done = True
            break

        try:
            chunk = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue

        # Извлекаем аудио из структуры дельты
        choices = chunk.get("choices") or [{}]
        delta = choices[0].get("delta", {})
        audio = delta.get("audio") or {}

        if isinstance(audio, dict) and audio.get("data"):
            is_cumulative = bool(audio.get("cumulative") or audio.get("is_cumulative"))
            raw_index = audio.get("index") if audio.get("index") is not None else audio.get("offset")
            yield AudioChunk(
                data=audio["data"],
                index=int(raw_index) if raw_index is not None else None,
                is_cumulative=is_cumulative,
            )

    if not received_done:
        raise GenerationStreamError("Stream interrupted: terminal [DONE] event not received")


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
    stop=DEFAULT_RETRY_STOP,
    wait=default_retry_wait,
    retry=default_retry_predicate,
    before_sleep=_log_generation_retry,
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
    # Счетчик размера файла
    total_b64 = 0
    # Отслеживание позиции в кумулятивном потоке кадров
    cumulative_cursor = 0
    # Флаг получения хотя бы одного аудио-чанка
    has_audio = False
    # Точка отсчета таймера для прогресс-бара
    started: float = time.monotonic()

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
            raise GenerationAPIError(
                f"OpenRouter {resp.status_code}: {error_body[:300]}",
                status_code=resp.status_code,
                headers=getattr(resp, "headers", None),
            )

        # Читаем очищенный поток из парсера.
        # Любой сбой во время чтения стрима — post-request ошибка,
        # которая не должна приводить к повторному POST-запросу.
        try:
            async for chunk in _parse_openrouter_sse(resp):
                has_audio = True
                if chunk.is_cumulative:
                    # Кумулятивный поток: кадр содержит данные с начала потока
                    if chunk.index is not None:
                        increment = chunk.data[chunk.index :]
                        cumulative_cursor = chunk.index + len(increment)
                    elif len(chunk.data) > cumulative_cursor:
                        increment = chunk.data[cumulative_cursor:]
                        cumulative_cursor = len(chunk.data)
                    else:
                        increment = ""
                    pending_b64 += increment
                    total_b64 += len(increment)
                else:
                    # Поток чистых дельт: каждый чанк добавляется в аудиопоток
                    pending_b64 += chunk.data
                    total_b64 += len(chunk.data)

                if total_b64 > MAX_AUDIO_B64_LEN:
                    raise GenerationStreamError("Audio in stream exceeds the allowed size")

                # base64 декодируется группами по 4 символа: готовую часть
                # сразу пишем в BytesIO, хвост ждёт следующих чанков
                aligned_len = len(pending_b64) - len(pending_b64) % 4
                if aligned_len:
                    decoded_audio.write(base64.b64decode(pending_b64[:aligned_len]))
                    pending_b64 = pending_b64[aligned_len:]

                # Обновляем UI асимптотически от времени
                elapsed = time.monotonic() - started
                fraction = 1.0 - math.exp(-elapsed / settings.generation.TYPICAL_GENERATION_SECONDS)
                await report(stage="Получаю аудио…", fraction=fraction * 0.95)
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            # КРИТИЧНО: post-request ошибки не повторяются (избегаем двойного списания)
            log.warning(
                "Stream interrupted (gen_id=%s, phase=post-request) due to %s: %s. No retry will be attempted.",
                gen_id,
                type(exc).__name__,
                exc,
            )
            raise GenerationStreamError(f"Stream interrupted during reading: {exc}") from exc

    if not has_audio:
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


async def persist_generated_title(user_id: int, title: str) -> None:
    """Сохраняет название и успешный статус в ожидающую запись генерации.

    Args:
        user_id: Telegram user_id пользователя.
        title: Название песни.

    Raises:
        SQLAlchemyError: При ошибке записи в БД.
    """
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Generation)
                .where(
                    Generation.user_id == user_id,
                    Generation.status == GenerationStatus.PENDING,
                )
                .order_by(Generation.created_at.desc(), Generation.id.desc())
                .limit(1)
            )
            generation = result.scalar_one_or_none()
            if generation is None:
                log.warning("Pending generation record not found (user=%s)", user_id)
                return

            generation.title = title
            generation.status = GenerationStatus.SUCCESS
            await session.commit()
    except Exception:
        log.exception("Failed to persist generated title (user=%s)", user_id, exc_info=True)
        raise


async def save_audio_to_storage(audio_bytes: bytes, gen_id: int) -> tuple[str, int, str]:
    """Сохраняет аудио на диск и возвращает метаданные файла.

    Args:
        audio_bytes: Байты аудио-файла.
        gen_id: ID генерации для формирования имени файла.

    Returns:
        Кортеж (путь к файлу, размер в байтах, SHA256 checksum).

    Raises:
        OSError: При ошибке записи файла.
    """
    storage_path = Path(settings.generation.AUDIO_STORAGE_PATH)
    storage_path.mkdir(parents=True, exist_ok=True)

    file_path = storage_path / f"gen_{gen_id}.mp3"
    file_path.write_bytes(audio_bytes)

    file_size = len(audio_bytes)
    checksum = hashlib.sha256(audio_bytes).hexdigest()

    return str(file_path), file_size, checksum
