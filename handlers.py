import asyncio
import logging
import httpx

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from aiogram.enums import ChatAction
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from sqlalchemy import select

import config
from database import SessionLocal
from models import User
from services import generation
from middlewares.auth import pending_auth

log = logging.getLogger(__name__)

router = Router()


def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎵 Сгенерировать")
    builder.button(text="💳 Кредиты")
    builder.button(text="🚪 Выйти")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


@router.message(Command("logout"))
@router.message(F.text == "🚪 Выйти")
async def cmd_logout(message: Message):
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
    await message.answer("👋 Ты вышел. /start чтобы войти снова.")


@router.message(Command("credits"))
@router.message(F.text == "💳 Кредиты")
async def cmd_credits(message: Message):
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
                return f"${int(value / 0.08)}" if isinstance(value, (int, float)) else msg

            total_songs = _songs_counter(value=total, msg="Без лимита")
            used_songs = _songs_counter(value=used, msg="0")
            remaining_songs = _songs_counter(value=remaining, msg="Пока не кончится бабосик или Влад не вспомнит про лимит 😁")

            await message.answer(
                f"💳 Кредиты OpenRouter:\n"
                f"Всего: {total_songs}\n"
                f"Использовано: {used_songs}\n"
                f"Осталось: {remaining_songs}",
                reply_markup=get_main_keyboard()
            )
    except Exception as e:
        log.exception("Credits check failed")
        await message.answer(f"❌ Не получилось проверить остатки: {e}")


@router.message(F.text == "🎵 Сгенерировать")
async def handle_text(message: Message):
    prompt = message.text.strip()
    if not prompt:
        return

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
        return

    file = BufferedInputFile(audio_bytes, filename="song.mp3")
    await message.answer_audio(file, caption="🎵 Готово!")
    await status.delete()
