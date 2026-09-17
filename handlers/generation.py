"""Процесс генерации песни: FSM-состояния, промпт, название, отправка аудио."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message

import config
from services import generation as generation_service

from .auth import _require_auth
from .keyboards import get_cancel_keyboard, get_main_keyboard
from .utils import notify_owner

log = logging.getLogger(__name__)

router = Router()

# Полный текст песни (куплеты + припевы) в среднем занимает 1500–3000 символов
MAX_PROMPT_LEN = 4000
MAX_TITLE_LEN = 100

# Минимальный интервал между правками сообщения прогресса (лимиты Telegram)
PROGRESS_EDIT_INTERVAL = 3.0


def _progress_bar(fraction: float, width: int = 10) -> str:
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


class GenerationStates(StatesGroup):
    """FSM-состояния процесса генерации песни."""

    waiting_for_prompt = State()
    waiting_for_title = State()


@router.message(F.text == "🎵 Сгенерировать")
async def cmd_generate(message: Message, state: FSMContext):
    """Старт генерации: показывает подсказку и запрашивает описание песни."""
    if not await _require_auth(message):
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
    """Отмена генерации по кнопке "❌ Отмена"."""
    await state.clear()
    await message.answer("❌ Генерация отменена.", reply_markup=get_main_keyboard())


@router.message(GenerationStates.waiting_for_prompt, F.text)
async def handle_prompt(message: Message, state: FSMContext):
    """Принимает и валидирует описание песни, запрашивает название."""
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
    """Принимает название, генерирует песню и отправляет аудиофайл."""
    title = message.text.strip()
    if not title:
        await message.answer("Пожалуйста, введите непустое название.")
        return
    if len(title) > MAX_TITLE_LEN:
        await message.answer(f"Слишком длинное название. Максимум {MAX_TITLE_LEN} символов.")
        return

    data = await state.get_data()
    prompt = data.get("prompt", "")
    await state.clear()

    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VOICE)
    status = await message.answer("🎼 Генерирую… Это может занять до 1–2 минут.")

    loop = asyncio.get_running_loop()
    last_edit = 0.0

    async def on_progress(stage: str, fraction: float) -> None:
        """Обновляет сообщение статуса не чаще, чем раз в PROGRESS_EDIT_INTERVAL сек."""
        nonlocal last_edit
        now = loop.time()
        if now - last_edit < PROGRESS_EDIT_INTERVAL:
            return
        last_edit = now
        text = f"🎼 {stage}\n{_progress_bar(fraction)} {int(fraction * 100)}%"
        try:
            await status.edit_text(text)
        except Exception:
            # Игнорируем "message is not modified" и прочие мелкие сбои правки
            pass

    try:
        if config.MOCK_MODE:
            # Режим заглушки для тестов без обращения к внешним API
            for i in (0.2, 0.5, 0.8):
                await on_progress("Генерирую (демо-режим)…", i)
                await asyncio.sleep(1)
            await on_progress("Собираю файл…", 0.97)
            audio_bytes = generation_service.load_mock_audio()
        else:
            audio_bytes = await generation_service.generate_song_real(prompt, on_progress)
    except Exception as e:
        await notify_owner(
            message.bot,
            f"Генерация упала (user={message.from_user.id}, title={title!r})",
            e,
        )
        await status.edit_text("😔 Не получилось сгенерировать. Попробуйте ещё раз чуть позже.")
        await message.answer("Выберите действие:", reply_markup=get_main_keyboard())
        return

    # Очищаем имя файла от спецсимволов, ограничиваем длину
    safe_title = "".join(c if c.isalnum() or c in "_-." else "_" for c in title)[:80] or "song"
    file = BufferedInputFile(audio_bytes, filename=f"{safe_title}.mp3")
    await message.answer_audio(file, caption="🎵 Готово!", reply_markup=get_main_keyboard())
    await status.delete()
