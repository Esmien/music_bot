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

# Таймаут ожидания захвата лока в секундах
DEFAULT_LOCK_TIMEOUT = 10.0

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
    retry_interval: float = 0.05,
) -> AsyncIterator[None]:
    """Асинхронный контекст: захват и освобождение распределённого Redis-лока.

    Лок реализован через `SET NX PX` с уникальным токеном владельца.
    Освобождение выполняется строго через атомарный Lua-скрипт (compare-and-delete).
    Ожидание захвата ограничено таймаутом через контекстный менеджер asyncio.timeout.

    Args:
        user_id: Telegram user_id пользователя.
        retry_interval: Интервал опроса Redis при ожидании блокировки в секундах.

    Yields:
        None.

    Raises:
        GenerationLockTimeoutError: Если лок не удалось захватить за отведённый таймаут.
    """
    lock_key = f"bot:generation_lock:{user_id}"
    owner_token = str(uuid.uuid4())

    try:
        async with asyncio.timeout(DEFAULT_LOCK_TIMEOUT):
            while not await redis_client.set(name=lock_key, value=owner_token, nx=True, px=LOCK_TTL_MS):
                await asyncio.sleep(retry_interval)
    except TimeoutError as err:
        raise GenerationLockTimeoutError(
            f"Failed to acquire generation lock for user {user_id} within {DEFAULT_LOCK_TIMEOUT}s"
        ) from err

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
                log.exception("Failed to release Redis lock for user %s: %s", user_id, e)
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


def _extract_audio_chunk(raw_payload: str) -> AudioChunk | None:
    """Извлекает AudioChunk из сырой полезной нагрузки SSE-строки.

    Args:
        raw_payload: Строка с JSON полезной нагрузки события SSE.

    Returns:
        Объект AudioChunk или None, если аудиоданные отсутствуют или некорректны.
    """
    try:
        chunk = json.loads(raw_payload)
    except json.JSONDecodeError:
        return None

    choices = chunk.get("choices") or [{}]
    delta = choices[0].get("delta", {})
    audio = delta.get("audio") or {}

    if not (isinstance(audio, dict) and audio.get("data")):
        return None

    is_cumulative = bool(audio.get("cumulative") or audio.get("is_cumulative"))
    raw_index = audio.get("index") if audio.get("index") is not None else audio.get("offset")
    return AudioChunk(
        data=audio["data"],
        index=int(raw_index) if raw_index is not None else None,
        is_cumulative=is_cumulative,
    )


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
            raise GenerationStreamError(f"SSE line exceeds limit: {len(line)} chars (max {MAX_SSE_LINE_LENGTH})"
)
            #raise GenerationStreamError(f"SSE line exceeds limit of {MAX_SSE_LINE_LENGTH} characters")

        if not line.startswith("data:"):
            continue

        # После "data:" может не быть пробела или их может быть несколько —
        # отрезаем префикс до первого двоеточия и чистим пробелы
        raw_payload = line.split(":", 1)[1].strip()
        if raw_payload == "[DONE]":
            received_done = True
            break

        chunk = _extract_audio_chunk(raw_payload=raw_payload)
        if chunk is not None:
            yield chunk

    if not received_done:
        raise GenerationStreamError("Stream interrupted: terminal [DONE] event not received")


def _extract_chunk_increment(chunk: AudioChunk, cumulative_cursor: int) -> tuple[str, int]:
    """Вычисляет добавленный фрагмент base64 и обновлённый курсор для чанка.

    Args:
        chunk: Очередной чанк аудиопотока.
        cumulative_cursor: Текущая позиция курсора в кумулятивном потоке.

    Returns:
        Кортеж из новой порции base64-строки и обновленного курсора.
    """
    if not chunk.is_cumulative:
        return chunk.data, cumulative_cursor

    if chunk.index is not None:
        increment = chunk.data[chunk.index :]
        return increment, chunk.index + len(increment)

    if len(chunk.data) > cumulative_cursor:
        increment = chunk.data[cumulative_cursor:]
        return increment, len(chunk.data)

    return "", cumulative_cursor


class _AudioStreamAccumulator:
    """Буфер для пошаговой сборки и декодирования аудиопотока из SSE-чанков."""

    def __init__(self) -> None:
        self.decoded_audio = io.BytesIO()
        self.pending_b64 = ""
        self.total_b64 = 0
        self.cumulative_cursor = 0
        self.has_audio = False

    def feed(self, chunk: AudioChunk) -> None:
        """Обрабатывает очередной AudioChunk и обновляет внутренний буфер.

        Args:
            chunk: Полученный чанк с данными аудио.

        Raises:
            GenerationStreamError: Если размер аудио превысил лимит MAX_AUDIO_B64_LEN.
        """
        self.has_audio = True
        increment, self.cumulative_cursor = _extract_chunk_increment(
            chunk=chunk,
            cumulative_cursor=self.cumulative_cursor,
        )

        self.pending_b64 += increment
        self.total_b64 += len(increment)
        if self.total_b64 > MAX_AUDIO_B64_LEN:
            raise GenerationStreamError("Audio in stream exceeds the allowed size")

        aligned_len = len(self.pending_b64) - len(self.pending_b64) % 4
        if aligned_len:
            self.decoded_audio.write(base64.b64decode(self.pending_b64[:aligned_len]))
            self.pending_b64 = self.pending_b64[aligned_len:]

    def finalize(self) -> bytes:
        """Сбрасывает остаток base64 и возвращает готовое аудио.

        Returns:
            Байты готового mp3-файла.

        Raises:
            GenerationAudioMissingError: Если аудио не получено в потоке.
        """
        if not self.has_audio:
            raise GenerationAudioMissingError("No audio received in stream")
        if self.pending_b64:
            self.decoded_audio.write(base64.b64decode(self.pending_b64))
            self.pending_b64 = ""
        return self.decoded_audio.getvalue()


async def _consume_generation_stream(
    response: httpx.Response,
    gen_id: int,
    started: float,
    report: ProgressCallback,
) -> bytes:
    """Вычитывает SSE-поток аудио и передаёт прогресс.

    Args:
        response: HTTP-ответ от OpenRouter со стримом.
        gen_id: ID генерации для логирования сбоев.
        started: Точка отсчета времени для расчета прогресса.
        report: Коллбэк для отправки прогресса.

    Returns:
        Собранные байты mp3-файла.

    Raises:
        GenerationStreamError: Если чтение потока прервано ошибкой сети или превышен лимит размера.
        GenerationAudioMissingError: Если в потоке не было получено аудиоданных.
    """
    accumulator = _AudioStreamAccumulator()
    try:
        async for chunk in _parse_openrouter_sse(response=response):
            accumulator.feed(chunk=chunk)
            elapsed = time.monotonic() - started
            fraction = 1.0 - math.exp(-elapsed / settings.generation.TYPICAL_GENERATION_SECONDS)
            await report(stage="Получаю аудио…", fraction=fraction * 0.95)
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        log.warning(
            "Stream interrupted (gen_id=%s, phase=post-request) due to %s: %s. No retry will be attempted.",
            gen_id,
            type(exc).__name__,
            exc,
        )
        raise GenerationStreamError(f"Stream interrupted during reading: {exc}") from exc

    return accumulator.finalize()


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

        audio_bytes = await _consume_generation_stream(
            response=resp,
            gen_id=gen_id,
            started=started,
            report=report,
        )

    await report(stage="Собираю файл…", fraction=0.97)
    return audio_bytes


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


def save_audio_to_storage(audio_bytes: bytes, gen_id: int) -> tuple[str, int, str]:
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
