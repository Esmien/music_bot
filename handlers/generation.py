"""Процесс генерации песни: FSM-состояния, промпт, название, отправка аудио."""

import asyncio
import contextlib
import logging
from uuid import uuid4

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

import config
from services import generation as generation_service

from .auth import _require_auth, is_authorized
from .keyboards import get_cancel_keyboard, get_main_keyboard
from .state import active_tasks
from .utils import notify_owner

log = logging.getLogger(__name__)

router = Router()

# Полный текст песни (куплеты + припевы) в среднем занимает 1500–3000 символов
MAX_PROMPT_LEN = 4000
MAX_TITLE_LEN = 100

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


async def _generate_and_send(
    message: Message, state: FSMContext, prompt: str, title: str, user_id: int
) -> None:
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
    task = asyncio.current_task()
    try:
        # Атомарная проверка-и-установка флага generating (см. _generation_locks).
        async with _generation_lock(user_id):
            if (await state.get_data()).get("generating"):
                await message.answer(
                    "⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена»."
                )
                return
            # Маркер текущей генерации: защищает от параллельных запусков и от
            # затирания нового FSM-состояния поздно завершившейся старой генерацией.
            gen_id = uuid4().hex
            await state.update_data(generating=True, gen_id=gen_id)
            # Регистрируем задачу под локом: «активной» становится только та
            # генерация, что прошла проверку, — иначе cancel() погасил бы
            # задачу, которая лишь ждёт лок, а не реально генерирует.
            active_tasks[user_id] = task

        await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_DOCUMENT)
        status = await message.answer("🎼 Генерирую… Это может занять до 1–2 минут.")

        loop = asyncio.get_running_loop()
        # Отрицательный старт, чтобы первый же вызов on_progress не отсеялся интервалом
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

        try:
            if config.MOCK_MODE:
                for i in (0.2, 0.5, 0.8):
                    await on_progress("Генерирую (демо-режим)…", i)
                    # Спим дольше интервала правки, иначе демо-прогресс не виден
                    await asyncio.sleep(PROGRESS_EDIT_INTERVAL + 0.1)
                await on_progress("Собираю файл…", 0.97)
                audio_bytes = generation_service.load_mock_audio()
            else:
                audio_bytes = await generation_service.generate_song_real(prompt, on_progress)
        except asyncio.CancelledError:
            # Честная отмена (из cmd_cancel_generation / cmd_logout): убираем
            # «ползущее» сообщение прогресса и пробрасываем CancelledError —
            # задача обязана завершиться именно как отменённая, иначе
            # рассинхрон с event-loop (task.cancelled() == False).
            with contextlib.suppress(Exception):
                await status.delete()
            # Флаг обычно уже стёрт state.clear() в отменившем хендлере; снимаем
            # сами, только если состояние всё ещё наше (отмена пришла раньше).
            if (await state.get_data()).get("gen_id") == gen_id:
                await state.update_data(generating=False)
            raise
        except Exception as e:
            # Сбой уведомления не должен лишить пользователя кнопки ретрая
            with contextlib.suppress(Exception):
                await notify_owner(
                    message.bot,
                    f"Генерация упала (user={user_id}, title={title!r})",
                    e,
                )
            # Состояние НЕ чистим: prompt и title остались в FSM, повтор бесплатный.
            # Флаг снимаем только если генерация всё ещё актуальна.
            if (await state.get_data()).get("gen_id") == gen_id:
                await state.update_data(generating=False)
            retry_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="retry_generation")]
                ]
            )
            with contextlib.suppress(Exception):
                await status.edit_text(
                    "😔 Не получилось сгенерировать. Попробуйте ещё раз чуть позже.",
                    reply_markup=retry_kb,
                )
            return

        # Чистим состояние, только если это всё ещё актуальная генерация:
        # иначе «поздний» успех после «❌ Отмена» затрёт состояние новой сессии.
        if (await state.get_data()).get("gen_id") == gen_id:
            await state.clear()
        safe_title = "".join(c if c.isalnum() or c in "_-." else "_" for c in title)[:80] or "song"
        file = BufferedInputFile(audio_bytes, filename=f"{safe_title}.mp3")
        await message.answer_audio(file, caption="🎵 Готово!", reply_markup=get_main_keyboard())
        await status.delete()
    finally:
        # Снимаем регистрацию только своей записи: за время генерации могла
        # начаться новая (другая задача) — её запись не трогаем.
        if active_tasks.get(user_id) is task:
            active_tasks.pop(user_id, None)


class GenerationStates(StatesGroup):
    """FSM-состояния процесса генерации песни.

    Атрибуты:
        waiting_for_prompt: Ждём описание/текст песни.
        waiting_for_title: Ждём название трека.
    """

    waiting_for_prompt = State()
    waiting_for_title = State()


@router.message(F.text == "🎵 Сгенерировать")
async def cmd_generate(message: Message, state: FSMContext):
    """Старт генерации: показывает подсказку и запрашивает описание песни.

    Доступна только авторизованным и не во время идущей генерации.
    Шлёт два сообщения подряд: сначала расширенную подсказку, затем
    копируемый шаблон в <code> — так его удобно вставить и заполнить.
    После этого переводит FSM в ожидание описания песни.

    Args:
        message: Входящее сообщение (кнопка «🎵 Сгенерировать»).
        state: FSM-контекст текущего пользователя.
    """
    if not await _require_auth(message):
        return

    if (await state.get_data()).get("generating"):
        await message.answer("⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
        return

    text = (
        "✍️ <b>Опишите песню, которую хотите услышать.</b>\n\n"
        "Можно просто прислать стихи — музыку подберу сам.\n"
        "А можно подсказать, как именно она должна звучать.\n\n"
        "<blockquote expandable>"
        "<b>Что можно указать:</b>\n\n"
        "<b>Жанр и стиль</b>\n"
        "    русский рок, эстрада 80-х, авторская песня, "
        "частушки, романс\n\n"
        "<b>Настроение</b>\n"
        "    весёлое, грустное, задумчивое, озорное, "
        "торжественное\n\n"
        "<b>Инструменты</b>\n"
        "    гитара и баян, фортепиано и скрипка, "
        "только акустика, с барабанами\n\n"
        "<b>Темп и ритм</b>\n"
        "    медленно и плавно, быстро и зажигательно, "
        "в ритме вальса, марш\n\n"
        "<b>Голос</b>\n"
        "    женский, мягкий и нежный, на русском языке\n\n"
        "<b>Текст песни</b>\n"
        "    ваши стихи или тема, о чём петь\n"
        "</blockquote>\n"
        "💡 <i>Достаточно заполнить 2–3 пункта — "
        "остальное додумаю сам.</i>\n\n"
        "<b>Шаблон — нажмите, чтобы скопировать:</b>"
    )

    template = "Жанр: \nНастроение: \nИнструменты: \nТемп и ритм: \nГолос: \nТекст песни: "

    await message.answer(text=text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await message.answer(f"<code>{template}</code>", parse_mode="HTML")

    await state.set_state(GenerationStates.waiting_for_prompt)


@router.message(GenerationStates.waiting_for_prompt, F.text == "❌ Отмена")
@router.message(GenerationStates.waiting_for_title, F.text == "❌ Отмена")
async def cmd_cancel_generation(message: Message, state: FSMContext):
    """Отмена генерации по кнопке «❌ Отмена».

    Гасит живую задачу генерации из active_tasks (та удалит сообщение
    прогресса и завершится как отменённая), сбрасывает FSM и возвращает
    основную клавиатуру.

    Args:
        message: Входящее сообщение с кнопкой «❌ Отмена».
        state: FSM-контекст текущего пользователя.
    """
    # Гасим живую задачу генерации, если она есть: иначе она продолжит крутиться
    # и позже «внезапно» пришлёт песню или «😔 Не получилось» поверх отмены.
    task = active_tasks.get(message.from_user.id)
    if task is not None and not task.done():
        task.cancel()
    await state.clear()
    await message.answer("❌ Генерация отменена.", reply_markup=get_main_keyboard())


@router.message(GenerationStates.waiting_for_prompt, F.text)
async def handle_prompt(message: Message, state: FSMContext):
    """Принимает и валидирует описание песни, запрашивает название.

    Если пользователь заполнил шаблон (есть маркеры вида "Жанр:"),
    промпт оборачивается в бриф с явным указанием петь по-русски.
    Если пришли просто стихи — используется более мягкая формулировка.
    Подготовленный промпт сохраняется в FSM, состояние переключается
    на ожидание названия.

    Args:
        message: Входящее сообщение с описанием песни.
        state: FSM-контекст текущего пользователя.
    """
    prompt = message.text.strip()

    if not prompt:
        await message.answer("Пожалуйста, введите непустой текст.")
        return
    if len(prompt) > MAX_PROMPT_LEN:
        await message.answer(f"Слишком длинный текст: {len(prompt)} символов. Максимум — {MAX_PROMPT_LEN}.")
        return

    if any(marker in prompt for marker in ("Жанр:", "Настроение:", "Голос:")):
        # Пользователь заполнил шаблон — оставляем структуру
        structured_prompt = (
            "Create a song based on the following brief. "
            "If the lyrics are provided in Russian, sing in Russian.\n\n"
            f"{prompt}"
        )
    else:
        # Пришли просто стихи — не пугаем модель словом "brief"
        structured_prompt = (
            f"Create a song based on these lyrics. If the lyrics are in Russian, sing in Russian.\n\n{prompt}"
        )

    await state.update_data(prompt=structured_prompt)
    await state.set_state(GenerationStates.waiting_for_title)
    await message.answer("🎤 Введите название песни:", reply_markup=get_cancel_keyboard())


@router.message(GenerationStates.waiting_for_title, F.text)
async def handle_title(message: Message, state: FSMContext):
    """Принимает название, генерирует песню и отправляет аудиофайл.

    Валидирует название, сохраняет его в FSM (пригодится для повтора
    после сбоя) и запускает фоновую задачу _generate_and_send.

    Args:
        message: Входящее сообщение с названием песни.
        state: FSM-контекст текущего пользователя.
    """
    title = message.text.strip()
    if not title:
        await message.answer("Пожалуйста, введите непустое название.")
        return
    if len(title) > MAX_TITLE_LEN:
        await message.answer(f"Слишком длинное название. Максимум {MAX_TITLE_LEN} символов.")
        return

    data = await state.get_data()
    if data.get("generating"):
        await message.answer("⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
        return
    prompt = data.get("prompt", "")
    # title кладём в FSM — пригодится для retry
    await state.update_data(title=title)

    await _generate_and_send(message, state, prompt, title, message.from_user.id)


@router.callback_query(F.data == "retry_generation")
async def retry_generation(callback: CallbackQuery, state: FSMContext):
    """Перезапуск генерации с сохранёнными prompt и title после сбоя.

    Доступен только авторизованным и не во время идущей генерации:
    кнопка повтора могла остаться в чате после logout или запуска
    новой генерации. Если сохранённый промпт потерялся (например,
    после перезапуска бота), предлагает начать заново. Перед запуском
    удаляет старое сообщение с кнопкой, чтобы не плодить «😔» в истории.

    Args:
        callback: Нажатие на кнопку «🔄 Попробовать снова».
        state: FSM-контекст текущего пользователя.
    """
    # Кнопка могла остаться в чате после logout — без этой проверки
    # отозванный ключ позволил бы продолжать генерацию.
    if not await is_authorized(callback.from_user.id):
        await callback.answer("Доступ закрыт. Авторизуйтесь заново: /start", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    if data.get("generating"):
        await callback.answer("Генерация уже идёт.", show_alert=True)
        return

    prompt = data.get("prompt", "")
    title = data.get("title", "")

    if not prompt:
        # Состояние потерялось (перезапуск бота?) — честно просим начать заново
        await callback.answer("Начните заново: 🎵 Сгенерировать", show_alert=True)
        await state.clear()
        return

    # Старое сообщение с кнопкой удаляем, чтобы не плодить «😔» в истории
    with contextlib.suppress(Exception):
        await callback.message.delete()
    await callback.answer()

    await _generate_and_send(callback.message, state, prompt, title, callback.from_user.id)
