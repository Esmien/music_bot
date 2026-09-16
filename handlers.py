import asyncio
import logging
import secrets

import httpx
from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import config
from database import SessionLocal
from models import User
from services import generation

log = logging.getLogger(__name__)

router = Router()

MAX_PROMPT_LEN = 500
MAX_TITLE_LEN = 100

# Ожидающие пользователи временно "живут" в памяти
pending_auth: set[int] = set()


class GenerationStates(StatesGroup):
    waiting_for_prompt = State()
    waiting_for_title = State()


class IsPendingAuth(Filter):
    """Явный фильтр вместо лямбды — не падает на сервисных апдейтах без from_user."""

    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id in pending_auth


class NotCommand(Filter):
    """True, если сообщение не начинается с '/'."""

    async def __call__(self, message: Message) -> bool:
        text = message.text or ""
        return not text.startswith("/")


async def is_authorized(uid: int) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        return bool(db_user and db_user.is_authorized)


async def _require_auth(message: Message) -> bool:
    """Возвращает True, если юзер авторизован. Иначе отвечает и возвращает False."""
    uid = message.from_user.id
    if uid in pending_auth:
        await message.answer("Сначала отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return False
    if not await is_authorized(uid):
        await message.answer("Сначала отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return False
    return True


def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎵 Сгенерировать")
    builder.button(text="💳 Кредиты")
    builder.button(text="🚪 Выйти")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    return builder.as_markup(resize_keyboard=True)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        if db_user and db_user.is_authorized:
            await message.answer(
                "👋 Привет! Я бот для генерации песен.\nИспользуйте кнопки ниже для управления.",
                reply_markup=get_main_keyboard(),
            )
        else:
            pending_auth.add(uid)
            await message.answer(
                "👋 Привет! Для использования бота отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove()
            )


@router.message(Command("logout"))
@router.message(F.text == "🚪 Выйти")
async def cmd_logout(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    pending_auth.discard(uid)
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        if db_user:
            db_user.is_authorized = False
            session.add(db_user)
            await session.commit()
    log.info("User %s logged out", uid)
    await message.answer("👋 Вы вышли. /start чтобы войти снова.", reply_markup=ReplyKeyboardRemove())


@router.message(Command("credits"))
@router.message(F.text == "💳 Кредиты")
async def cmd_credits(message: Message, state: FSMContext):
    if not await _require_auth(message):
        return
    await state.clear()
    if not config.OPENROUTER_API_KEY:
        await message.answer("⚠️ Ключ OpenRouter не настроен.")
        return
    headers = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/key", headers=headers)
            if resp.status_code != 200:
                await message.answer(f"❌ Ошибка запроса: {resp.status_code}")
                return

            data = resp.json()
            credits_ = data.get("data", data)

            total = credits_.get("limit")
            remaining = credits_.get("limit_remaining")
            used = credits_.get("usage")

            def _songs_counter(value, msg):
                return int(value / 0.08) if isinstance(value, (int, float)) else msg

            total_songs = _songs_counter(value=total, msg="Без лимита")
            used_songs = _songs_counter(value=used, msg="0")
            remaining_songs = _songs_counter(
                value=remaining, msg="Пока не кончится бабосик или Влад не вспомнит про лимит 😁"
            )

            await message.answer(
                f"💳 Кредиты OpenRouter:\n"
                f"Всего: {total_songs}\n"
                f"Использовано: {used_songs}\n"
                f"Осталось: {remaining_songs}",
                reply_markup=get_main_keyboard(),
            )
    except Exception as e:
        log.exception("Credits check failed")
        await message.answer(f"❌ Не получилось проверить остатки: {e}")


@router.message(F.text == "🎵 Сгенерировать")
async def cmd_generate(message: Message, state: FSMContext):
    if not await _require_auth(message):
        return
    await message.answer("✍️ Введите описание для генерации песни:", reply_markup=get_cancel_keyboard())
    await state.set_state(GenerationStates.waiting_for_prompt)


@router.message(GenerationStates.waiting_for_prompt, F.text == "❌ Отмена")
@router.message(GenerationStates.waiting_for_title, F.text == "❌ Отмена")
async def cmd_cancel_generation(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Генерация отменена.", reply_markup=get_main_keyboard())


@router.message(GenerationStates.waiting_for_prompt, F.text)
async def handle_prompt(message: Message, state: FSMContext):
    prompt = message.text.strip()
    if not prompt:
        await message.answer("Пожалуйста, введите непустой текст.")
        return
    if len(prompt) > MAX_PROMPT_LEN:
        await message.answer(f"Слишком длинный текст. Максимум {MAX_PROMPT_LEN} символов.")
        return
    await state.update_data(prompt=prompt)
    await state.set_state(GenerationStates.waiting_for_title)
    await message.answer("🎤 Введите название песни:", reply_markup=get_cancel_keyboard())


@router.message(GenerationStates.waiting_for_title, F.text)
async def handle_title(message: Message, state: FSMContext):
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

    bot = message.bot
    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VOICE)
    status = await message.answer("🎼 Генерирую… Это может занять до 1–2 минут.")

    try:
        if config.MOCK_MODE:
            await asyncio.sleep(3)
            audio_bytes = generation.load_mock_audio()
        else:
            audio_bytes = await generation.generate_song_real(prompt)
    except Exception as e:
        log.exception("Generation failed")
        await status.edit_text(f"❌ Не получилось: {e}")
        await message.answer("Выберите действие:", reply_markup=get_main_keyboard())
        return

    safe_title = "".join(c if c.isalnum() or c in "_-." else "_" for c in title)[:80] or "song"
    file = BufferedInputFile(audio_bytes, filename=f"{safe_title}.mp3")
    await message.answer_audio(file, caption="🎵 Готово!", reply_markup=get_main_keyboard())
    await status.delete()


@router.message(F.text, NotCommand(), IsPendingAuth())
async def handle_key(message: Message):
    uid = message.from_user.id
    key = message.text.strip()

    if not key:
        await message.answer("Пожалуйста, отправьте ключ.")
        return

    expected_key = config.BOT_ACCESS_KEY
    if not expected_key:
        log.error("BOT_ACCESS_KEY не настроен — авторизация невозможна")
        await message.answer("⚠️ Бот не настроен. Сообщите владельцу.")
        return

    if not secrets.compare_digest(key, expected_key):
        log.warning("Bad auth attempt from user %s", uid)
        await message.answer("❌ Неверный ключ.")
        return

    # Upsert без гонки: add + flush, при конфликте — update
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        if db_user:
            db_user.is_authorized = True
        else:
            try:
                session.add(User(tg_id=uid, is_authorized=True))
                await session.flush()
            except IntegrityError:
                await session.rollback()
                result = await session.execute(select(User).where(User.tg_id == uid))
                db_user = result.scalar_one_or_none()
                if db_user:
                    db_user.is_authorized = True
        await session.commit()

    pending_auth.discard(uid)
    await message.answer("✅ Вы успешно авторизованы!", reply_markup=get_main_keyboard())


@router.message(F.text, NotCommand())
async def fallback(message: Message):
    uid = message.from_user.id
    if uid in pending_auth:
        return  # пусть обработает handle_key, он выше по приоритету
    if not await is_authorized(uid):
        await message.answer(
            "🔒 Сначала /start и введи ключ доступа.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await message.answer("Не понял. Используйте кнопки внизу.", reply_markup=get_main_keyboard())
