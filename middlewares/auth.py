import logging
import secrets
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from sqlalchemy import select

import config
from database import SessionLocal
from models import User

log = logging.getLogger(__name__)

# Те, кому бот только что предложил ввести ключ (временное in-memory состояние)
pending_auth: set[int] = set()


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user = event.from_user
        if user is None:
            return

        uid = user.id

        async with SessionLocal() as session:
            result = await session.execute(select(User).where(User.tg_id == uid))
            db_user = result.scalar_one_or_none()
            if db_user and db_user.is_authorized:
                return await handler(event, data)

        text = (event.text or "").strip()

        if text.startswith("/start"):
            pending_auth.add(uid)
            await event.answer(
                "🔒 Это приватный бот.\n\n"
                "Отправь мне ключ доступа, чтобы продолжить."
            )
            return

        if uid in pending_auth:
            if not config.BOT_ACCESS_KEY:
                await event.answer("⚠️ Владелец бота не настроил ключ доступа.")
                return
            if secrets.compare_digest(text, config.BOT_ACCESS_KEY):
                async with SessionLocal() as session:
                    result = await session.execute(select(User).where(User.tg_id == uid))
                    db_user = result.scalar_one_or_none()
                    if db_user:
                        db_user.is_authorized = True
                        session.add(db_user)
                    else:
                        db_user = User(tg_id=uid, is_authorized=True)
                        session.add(db_user)
                    await session.commit()
                pending_auth.discard(uid)
                log.info("User %s authorized", uid)
                await event.answer(
                    "✅ Доступ разрешён.\n\n"
                    "Пришли стих — сделаю из него песню.\n"
                    "Команда /logout — выйти."
                )
            else:
                log.warning("Failed auth attempt from user %s", uid)
                await event.answer("❌ Неверный ключ. Попробуй ещё раз.")
            return

        await event.answer("🔒 Сначала /start и введи ключ доступа.")
