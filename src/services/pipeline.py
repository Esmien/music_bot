"""Сервисный слой конвейера генерации: запуск, прогресс и пер-пользовательские локи.

Модуль ничего не знает о хендлерах, FSM и Telegram: прогресс отдаётся
через абстрактный колбэк on_progress, а «отрисовка» текста — через
произвольную корутину, переданную вызывающей стороной.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from core.config import settings
from services.generation import ProgressCallback, generate_song_real, load_mock_audio

# Минимальный интервал между правками сообщения прогресса (лимиты Telegram)
PROGRESS_EDIT_INTERVAL = 3.0

# Пер-пользовательские локи: превращают проверку-и-установку флага generating
# в атомарную — иначе два параллельных апдейта оба пройдут проверку.
# Записи чистятся при освобождении слота (см. user_generation_lock)
_generation_locks: dict[int, asyncio.Lock] = {}


async def _acquire_user_lock(user_id: int) -> asyncio.Lock:
    """Захватывает актуальный пер-пользовательский лок (validate-after-acquire).

    Чистка словаря разрешена гонкам: если между получением записи из словаря
    и acquire() запись была удалена/заменена, захваченный лок признаётся
    протухшим, отпускается, и захват повторяется уже под актуальным локом.

    Args:
        user_id: Telegram user_id пользователя.

    Returns:
        Актуальный (всё ещё зарегистрированный в словаре) захваченный лок.
    """
    while True:
        lock = _generation_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            existing = _generation_locks.setdefault(user_id, lock)
            if existing is not lock:
                continue  # нас обогнали — берём актуальную запись
        await lock.acquire()
        if _generation_locks.get(user_id) is lock:
            return lock  # лок актуален — работаем
        lock.release()  # протух — идём за актуальным


@asynccontextmanager
async def user_generation_lock(user_id: int) -> AsyncIterator[None]:
    """Асинхронный контекст: захват и освобождение слота генерации пользователя.

    При выходе снимает лок и удаляет запись из словаря, если лок свободен
    и всё ещё является актуальной записью. Гонку чистки с параллельным
    захватом гасит validate-after-acquire в _acquire_user_lock: корутина,
    захватившая «протухший» лок, сама его отпустит и повторит захват.

    Args:
        user_id: Telegram user_id пользователя.
    """
    lock = await _acquire_user_lock(user_id=user_id)
    try:
        yield
    finally:
        lock.release()
        # Чистим запись, только если это всё ещё актуальный и свободный лок.
        # Даже если здесь мы удалим лок, на котором кто-то ждёт, — ждущий
        # отсеется повторной проверкой в _acquire_user_lock
        if _generation_locks.get(user_id) is lock and not lock.locked():
            _generation_locks.pop(user_id, None)


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


async def run_generation(prompt: str, on_progress: ProgressCallback) -> bytes:
    """Запускает генерацию: демо-ветка в MOCK_MODE или реальный сервис.

    Args:
        prompt: Промпт для модели (описание песни).
        on_progress: Корутина `on_progress(stage, fraction)`.

    Returns:
        Байты готового аудио.
    """
    if settings.generation.MOCK_MODE:
        # Для демо-режима отображаем прогресс с шагом 30%
        for fraction in (0.2, 0.5, 0.8):
            await on_progress(stage="Генерирую (демо-режим)…", fraction=fraction)
            # Спим дольше интервала правки, иначе демо-прогресс не виден
            await asyncio.sleep(PROGRESS_EDIT_INTERVAL + 0.1)

        # Имитируем сборку и отдаем аудио из mock-файла
        await on_progress(stage="Собираю файл…", fraction=0.97)
        return load_mock_audio()

    # Отдаем реально сгенерированный файл, если генерация шла через API
    return await generate_song_real(prompt=prompt, on_progress=on_progress)
