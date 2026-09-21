"""Движок генерации песни: запуск, прогресс, отмена и обработка сбоев.

Вынесено из хендлеров, чтобы те занимались только FSM-диалогом.
Модуль не знает о роутере: он умеет «сгенерировать и
отправить» одну песню, честно обработать отмену и сбой и не дать
одному пользователю запустить две генерации параллельно.
"""

import asyncio
import contextlib
import logging
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

from config import settings
from fsm.evaluation_fsm import active_tasks
from keyboards.default_keyboards import get_main_keyboard
from services import generation as generation_service
from services.generation import ProgressCallback
from utils.error_notify import notify_owner

log = logging.getLogger(__name__)

# Минимальный интервал между правками сообщения прогресса (лимиты Telegram)
PROGRESS_EDIT_INTERVAL = 3.0

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


async def _is_actual_gen(state: FSMContext, gen_id: str) -> bool:
    """Проверяет, актуальна ли генерация в стейте

    Args:
        state: состояние FSM пользователя
        gen_id: id генерации из контекста

    Returns:
        True, если актуальна
    """
    return (await state.get_data()).get("gen_id") == gen_id


@dataclass
class GenerationContext:
    """Контекст одного запуска генерации.

    Атрибуты:
        message: Сообщение, от имени которого шлются статусы и аудио.
        state: FSM-контекст пользователя.
        prompt: Подготовленное описание песни.
        title: Название трека (используется в имени файла).
        user_id: Telegram user_id пользователя.
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
        title: Название трека (используется в имени файла).
        user_id: Telegram user_id пользователя.
    """
    # Собираем весь контекст генерации в кучу
    gen_context = GenerationContext(
        message=message,
        state=state,
        prompt=prompt,
        title=title,
        user_id=user_id,
        task=asyncio.current_task(),
    )
    try:
        # Слот занят другой генерацией - не запускаем новую
        if not await _acquire_slot(gen_context=gen_context):
            return

        # Запускаем визуальный процесс генерации для пользователя
        status = await _start_status(gen_context=gen_context)
        on_progress = _make_progress_reporter(status=status)

        try:
            audio_bytes = await _run_generation(gen_context=gen_context, on_progress=on_progress)
        except asyncio.CancelledError:
            await _cleanup_cancelled(gen_context=gen_context, status=status)
            raise
        except Exception as error:
            await _handle_failure(gen_context=gen_context, status=status, error=error)
            return
        await _deliver_result(gen_context=gen_context, status=status, audio_bytes=audio_bytes)
    finally:
        _release_slot(gen_context=gen_context)


async def _acquire_slot(gen_context: GenerationContext) -> bool:
    """Атомарно занимает слот генерации пользователя.

    Под пер-пользовательским локом проверяет флаг generating и выставляет
    его вместе с маркером gen_id; регистрирует задачу в active_tasks —
    «активной» становится только та генерация, что прошла проверку, —
    иначе cancel() погасил бы задачу, которая лишь ждёт лок, а не
    реально генерирует.

    Args:
        gen_context: Контекст запуска генерации.

    Returns:
        True, если слот занят и генерацию можно запускать.
    """
    async with _generation_lock(user_id=gen_context.user_id):
        # Проверка, занят ли слот. Если занят, не даем запустить новую
        if (await gen_context.state.get_data()).get("generating"):
            await gen_context.message.answer(text="⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
            # Сообщаем, что слот занят, генерировать нельзя
            return False

        # Присваиваем маркер конкретной генерации
        gen_context.gen_id = uuid4().hex
        # Устанавливаем в FSM пользователя
        # статус "генерируется" и маркер самой генерации, занимая слот
        await gen_context.state.update_data(generating=True, gen_id=gen_context.gen_id)
        # Регистрируем в реестре текущих задач
        active_tasks[gen_context.user_id] = gen_context.task
        return True


async def _start_status(gen_context: GenerationContext) -> Message:
    """Показывает «бот отправляет файл…» и создаёт сообщение-статус с прогрессом."""
    await gen_context.message.bot.send_chat_action(
        chat_id=gen_context.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT
    )

    return await gen_context.message.answer(text="🎼 Генерирую… Это может занять до 1–2 минут.")


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
        """Коллбэк для отрисовки прогресс-бара

        Args:
            stage: название этапа сборки
            fraction: оценочная доля прогресса (0...1) для отображения в процентах
        """
        nonlocal last_edit

        # Троттлим: правки статуса не чаще PROGRESS_EDIT_INTERVAL (лимиты Telegram)
        now = loop.time()
        if now - last_edit < PROGRESS_EDIT_INTERVAL:
            return

        last_edit = now

        # Песня еще не пришла, формируем новый блок
        text = f"🎼 {stage}\n{_progress_bar(fraction)} {round(fraction * 100)}%"

        # Отрисовываем изменение
        with contextlib.suppress(Exception):
            # Позиционно: заглушки edit_text в тестах объявлены как (new_text, **kwargs)
            await status.edit_text(text)

    return on_progress


async def _run_generation(gen_context: GenerationContext, on_progress: ProgressCallback) -> bytes:
    """Запускает генерацию: демо-ветка в MOCK_MODE или реальный сервис.

    Args:
        gen_context: Контекст запуска генерации.
        on_progress: Корутин-колбек `on_progress(stage, fraction)`.

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
        return generation_service.load_mock_audio()

    # Отдаем реально сгенерированный файл, если генерация шла через API
    return await generation_service.generate_song_real(prompt=gen_context.prompt, on_progress=on_progress)


async def _cleanup_cancelled(gen_context: GenerationContext, status: Message) -> None:
    """Чистим экран пользователя после отмены генерации.
    Сама отмена приходит из cmd_cancel_generation / cmd_logout через task.cancel().

    Убираем «ползущее» сообщение прогресса.
    Флаг 'generating' обычно уже стёрт state.clear() в отменившем хендлере.
    Снимаем сами, только если gen_id в стейте еще актуален.
    Прилетевший CancelledError пропускаем дальше, его получит Task и
    отменит задачу

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
    """
    # Чистим экран пользователя от прогресс-бара
    with contextlib.suppress(Exception):
        await status.delete()

    # Снимаем флаг только если в FSM все еще текущая генерация
    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        await gen_context.state.update_data(generating=False)


async def _handle_failure(gen_context: GenerationContext, status: Message, error: Exception) -> None:
    """Обрабатывает сбой сервиса: уведомляет владельца и рисует кнопку повтора.

    Состояние НЕ чистим: prompt и title остались в FSM,
    повтор не требует сборки контекста еще раз.
    Флаг снимаем только если генерация всё ещё актуальна.
    Сбой самого уведомления не должен лишить пользователя кнопки ретрая.

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
        error: Исключение, упавшее из сервиса генерации.
    """
    with contextlib.suppress(Exception):
        await notify_owner(
            bot=gen_context.message.bot,
            context=f"Генерация упала (user={gen_context.user_id}, title={gen_context.title!r})",
            err=error,
        )

    # Защита только для флага: не глушим генерирующий флаг новой генерации.
    # Кнопка ретрая вешается всегда — контекст она возьмёт из FSM в момент нажатия.
    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        await gen_context.state.update_data(generating=False)
    retry_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="retry_generation")]]
    )

    # Даже если правка статуса не пройдёт — не роняем задачу, кнопка ретрая важнее
    with contextlib.suppress(Exception):
        await status.edit_text(
            "😔 Не получилось сгенерировать. Попробуйте ещё раз чуть позже.",
            reply_markup=retry_kb,
        )


async def _deliver_result(gen_context: GenerationContext, status: Message, audio_bytes: bytes) -> None:
    """Отправляет готовое аудио и чистит состояние после успеха.

    Состояние чистим, только если это всё ещё актуальная генерация:
    иначе «поздний» успех после «❌ Отмена» затрёт состояние новой сессии.

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
        audio_bytes: Байты готового аудио.
    """
    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        await gen_context.state.clear()

    # Санитайзинг названия песни, чтобы ТГ не сошел с ума от "левых" символов
    safe_title = "".join(c if c.isalnum() or c in "_-." else "_" for c in gen_context.title)[:80] or "song"

    # Сборка песни в файл, отправка пользователю и очистка экрана от прогресс-бара
    file = BufferedInputFile(file=audio_bytes, filename=f"{safe_title}.mp3")
    await gen_context.message.answer_audio(audio=file, caption="🎵 Готово!", reply_markup=get_main_keyboard())
    await status.delete()


def _release_slot(gen_context: GenerationContext) -> None:
    """Снимает регистрацию задачи в active_tasks.

    Убираем только свою запись: за время генерации могла начаться новая
    (другая задача) — её запись не трогаем.

    Args:
        gen_context: Контекст запуска генерации.
    """
    if active_tasks.get(gen_context.user_id) is gen_context.task:
        active_tasks.pop(gen_context.user_id, None)
