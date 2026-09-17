"""Движок генерации песни: запуск, прогресс, отмена и обработка сбоев.

Вынесено из хендлеров, чтобы те занимались только FSM-диалогом.
Модуль не знает о роутере и состояниях: он умеет «сгенерировать и
отправить» одну песню, честно обработать отмену и сбой и не дать
одному пользователю запустить две генерации параллельно.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from services import generation as generation_service

from .keyboards import get_main_keyboard
from .state import active_tasks
from .utils import notify_owner

log = logging.getLogger(__name__)

# Минимальный интервал между правками сообщения прогресса (лимиты Telegram)
PROGRESS_EDIT_INTERVAL = 3.0

# Колбек прогресса: `on_progress(stage, fraction)`, fraction в диапазоне 0..1
ProgressCallback = Callable[[str, float], Awaitable[None]]

# Пер-пользовательские локи: превращают проверку-и-установку флага generating
# в атомарную — иначе два параллельных апдейта оба пройдут проверку.
_generation_locks: dict[int, asyncio.Lock] = {}


def _generation_lock(user_id: int) -> asyncio.Lock:
    """Возвращает лок генерации для пользователя, создавая при необходимости."""
    lock = _generation_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _generation_locks[user_id] = lock
    return lock


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


@dataclass
class GenerationRun:
    """Контекст одного запуска генерации.

    Атрибуты:
        message: Сообщение, от имени которого шлются статусы и аудио.
        state: FSM-контекст пользователя.
        prompt: Подготовленное описание песни.
        title: Название трека (используется и в имени файла).
        user_id: Telegram user_id владельца генерации.
        task: Фоновая задача, в которой крутится генерация.
        gen_id: Маркер запуска; защищает от затирания нового FSM-состояния
            поздно завершившейся старой генерацией.
    """

    message: Message
    state: FSMContext
    prompt: str
    title: str
    user_id: int
    task: asyncio.Task
    gen_id: str = ""


async def generate_and_send(message: Message, state: FSMContext, prompt: str, title: str, user_id: int) -> None:
    """Генерирует аудио и отправляет его в чат.

    Запускается как фоновая задача из handle_title и retry_generation.
    Под локом проверяет и выставляет флаг generating в FSM (защита от
    параллельных запусков) и регистрирует себя в active_tasks, чтобы
    генерацию можно было погасить из cmd_cancel_generation / cmd_logout.
    По ходу дела правит сообщение-статус с прогресс-баром (не чаще
    PROGRESS_EDIT_INTERVAL). При ошибке оставляет prompt и title в FSM
    и вешает кнопку повтора; при отмене убирает сообщение прогресса и
    пробрасывает CancelledError. Успех завершается отправкой аудио и
    очисткой FSM — но только если генерация всё ещё актуальна.

    Args:
        message: Сообщение, от имени которого шлются статусы и аудио.
        state: FSM-контекст пользователя.
        prompt: Подготовленное описание песни.
        title: Название трека (используется и в имени файла).
        user_id: Telegram user_id владельца генерации.
    """
    run = GenerationRun(
        message=message,
        state=state,
        prompt=prompt,
        title=title,
        user_id=user_id,
        task=asyncio.current_task(),
    )
    try:
        if not await _acquire_slot(run):
            return
        status = await _start_status(run)
        on_progress = _make_progress_reporter(status)
        try:
            audio_bytes = await _run_generation(run, on_progress)
        except asyncio.CancelledError:
            await _cleanup_cancelled(run, status)
            raise
        except Exception as e:
            await _handle_failure(run, status, e)
            return
        await _deliver_result(run, status, audio_bytes)
    finally:
        _release_slot(run)


async def _acquire_slot(run: GenerationRun) -> bool:
    """Атомарно занимает слот генерации пользователя.

    Под пер-пользовательским локом проверяет флаг generating и выставляет
    его вместе с маркером gen_id; регистрирует задачу в active_tasks —
    «активной» становится только та генерация, что прошла проверку, —
    иначе cancel() погасил бы задачу, которая лишь ждёт лок, а не
    реально генерирует.

    Args:
        run: Контекст запуска генерации.

    Returns:
        True, если слот занят и генерацию можно запускать.
    """
    async with _generation_lock(run.user_id):
        if (await run.state.get_data()).get("generating"):
            await run.message.answer("⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
            return False
        run.gen_id = uuid4().hex
        await run.state.update_data(generating=True, gen_id=run.gen_id)
        active_tasks[run.user_id] = run.task
        return True


async def _start_status(run: GenerationRun) -> Message:
    """Показывает «бот печатает…» и создаёт сообщение-статус с прогрессом."""
    await run.message.bot.send_chat_action(run.message.chat.id, ChatAction.UPLOAD_DOCUMENT)
    return await run.message.answer("🎼 Генерирую… Это может занять до 1–2 минут.")


def _make_progress_reporter(status: Message) -> ProgressCallback:
    """Возвращает корутину on_progress, троттлящую правки статуса.

    Правки идут не чаще PROGRESS_EDIT_INTERVAL (лимиты Telegram);
    отрицательный старт гарантирует, что первый вызов не отсеется.

    Args:
        status: Сообщение-статус, которое редактируется по мере прогресса.
    """
    loop = asyncio.get_running_loop()
    last_edit = -PROGRESS_EDIT_INTERVAL

    async def on_progress(stage: str, fraction: float) -> None:
        nonlocal last_edit
        now = loop.time()
        if now - last_edit < PROGRESS_EDIT_INTERVAL:
            return
        last_edit = now
        text = f"🎼 {stage}\n{_progress_bar(fraction)} {int(fraction * 100)}%"
        with contextlib.suppress(Exception):
            await status.edit_text(text)

    return on_progress


async def _run_generation(run: GenerationRun, on_progress: ProgressCallback) -> bytes:
    """Запускает генерацию: демо-ветка в MOCK_MODE или реальный сервис.

    Args:
        run: Контекст запуска генерации.
        on_progress: Корутин-колбек `on_progress(stage, fraction)`.

    Returns:
        Байты готового аудио.
    """
    if config.MOCK_MODE:
        for i in (0.2, 0.5, 0.8):
            await on_progress("Генерирую (демо-режим)…", i)
            # Спим дольше интервала правки, иначе демо-прогресс не виден
            await asyncio.sleep(PROGRESS_EDIT_INTERVAL + 0.1)
        await on_progress("Собираю файл…", 0.97)
        return generation_service.load_mock_audio()
    return await generation_service.generate_song_real(run.prompt, on_progress)


async def _cleanup_cancelled(run: GenerationRun, status: Message) -> None:
    """Честная отмена (из cmd_cancel_generation / cmd_logout).

    Убираем «ползущее» сообщение прогресса; CancelledError пробрасывается
    дальше — задача обязана завершиться именно как отменённая, иначе
    рассинхрон с event-loop (task.cancelled() == False). Флаг обычно уже
    стёрт state.clear() в отменившем хендлере; снимаем сами, только если
    состояние всё ещё наше (отмена пришла раньше).

    Args:
        run: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
    """
    with contextlib.suppress(Exception):
        await status.delete()
    if (await run.state.get_data()).get("gen_id") == run.gen_id:
        await run.state.update_data(generating=False)


async def _handle_failure(run: GenerationRun, status: Message, error: Exception) -> None:
    """Обрабатывает сбой сервиса: уведомление владельца и кнопка повтора.

    Состояние НЕ чистим: prompt и title остались в FSM, повтор бесплатный.
    Флаг снимаем только если генерация всё ещё актуальна; сбой самого
    уведомления не должен лишить пользователя кнопки ретрая.

    Args:
        run: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
        error: Исключение, упавшее из сервиса генерации.
    """
    with contextlib.suppress(Exception):
        await notify_owner(
            run.message.bot,
            f"Генерация упала (user={run.user_id}, title={run.title!r})",
            error,
        )
    if (await run.state.get_data()).get("gen_id") == run.gen_id:
        await run.state.update_data(generating=False)
    retry_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="retry_generation")]]
    )
    with contextlib.suppress(Exception):
        await status.edit_text(
            "😔 Не получилось сгенерировать. Попробуйте ещё раз чуть позже.",
            reply_markup=retry_kb,
        )


async def _deliver_result(run: GenerationRun, status: Message, audio_bytes: bytes) -> None:
    """Отправляет готовое аудио и чистит состояние после успеха.

    Состояние чистим, только если это всё ещё актуальная генерация:
    иначе «поздний» успех после «❌ Отмена» затрёт состояние новой сессии.

    Args:
        run: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
        audio_bytes: Байты готового аудио.
    """
    if (await run.state.get_data()).get("gen_id") == run.gen_id:
        await run.state.clear()
    safe_title = "".join(c if c.isalnum() or c in "_-." else "_" for c in run.title)[:80] or "song"
    file = BufferedInputFile(audio_bytes, filename=f"{safe_title}.mp3")
    await run.message.answer_audio(file, caption="🎵 Готово!", reply_markup=get_main_keyboard())
    await status.delete()


def _release_slot(run: GenerationRun) -> None:
    """Снимает регистрацию задачи в active_tasks.

    Убираем только свою запись: за время генерации могла начаться новая
    (другая задача) — её запись не трогаем.

    Args:
        run: Контекст запуска генерации.
    """
    if active_tasks.get(run.user_id) is run.task:
        active_tasks.pop(run.user_id, None)
