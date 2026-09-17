"""Авторизация: ключ доступа, /start, /logout, fallback-обработчик."""

import logging
import secrets

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import config
from database import SessionLocal
from models import User

from .filters import IsPendingAuth, NotCommand
from .keyboards import get_main_keyboard
from .state import pending_auth

log = logging.getLogger(__name__)

router = Router()

# Защита от перебора ключа доступа: счётчик неудачных попыток на пользователя.
# Как и pending_auth, живёт в памяти и сбрасывается при перезапуске
MAX_KEY_ATTEMPTS = 5
failed_key_attempts: dict[int, int] = {}


async def is_authorized(uid: int) -> bool:
    """Проверяет по БД, авторизован ли пользователь.

    Args:
        uid: Telegram user_id.

    Returns:
        True, если пользователь найден и is_authorized=True.
    """
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        return bool(db_user and db_user.is_authorized)


async def _require_auth(message: Message) -> bool:
    """Гарантирует авторизацию перед действием.

    Args:
        message: Входящее сообщение от пользователя.

    Returns:
        True, если пользователь авторизован; иначе отправляет
        подсказку и возвращает False.
    """
    uid = message.from_user.id
    if uid in pending_auth or not await is_authorized(uid):
        await message.answer("Сначала отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return False
    return True


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """/start: приветствие и проверка статуса авторизации.

    Неавторизованным добавляет их в pending_auth — дальше
    ввод ключа перехватит handle_key через фильтр IsPendingAuth.
    """
    await state.clear()
    uid = message.from_user.id
    if await is_authorized(uid):
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
    """Выход: снимает авторизацию в БД и очищает состояние ожидания."""
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


@router.message(F.text, NotCommand(), IsPendingAuth())
async def handle_key(message: Message):
    """Обработка ключа доступа от неавторизованного пользователя."""
    uid = message.from_user.id
    key = message.text.strip()

    # Удаляем сообщение с ключом, чтобы он не оставался в истории чата
    try:
        await message.delete()
    except Exception:
        log.warning("Не удалось удалить сообщение с ключом (user=%s)", uid)

    expected_key = config.BOT_ACCESS_KEY
    if not expected_key:
        log.error("BOT_ACCESS_KEY не настроен — авторизация невозможна")
        await message.answer("⚠️ Бот не настроен. Сообщите владельцу.")
        return

    if not secrets.compare_digest(key.encode("utf-8"), expected_key.encode("utf-8")):
        attempts = failed_key_attempts.get(uid, 0) + 1
        failed_key_attempts[uid] = attempts
        if attempts >= MAX_KEY_ATTEMPTS:
            pending_auth.discard(uid)
            failed_key_attempts.pop(uid, None)
            log.warning("Исчерпаны попытки ввода ключа (user=%s)", uid)
            await message.answer("❌ Слишком много неверных попыток. Отправьте /start, чтобы начать заново.")
            return
        await message.answer("❌ Неверный ключ доступа.")
        return

    failed_key_attempts.pop(uid, None)

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        if db_user:
            db_user.is_authorized = True
        else:
            try:
                session.add(User(tg_id=uid, is_authorized=True))
                # flush вместо commit: IntegrityError поймаем до фиксации транзакции
                await session.flush()
            except IntegrityError:
                # На случай параллельной вставки того же пользователя
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
    """Fallback для нераспознанных текстовых сообщений (регистрируется последним)."""
    uid = message.from_user.id
    if uid in pending_auth:
        return  # пусть обработает handle_key
    if not await is_authorized(uid):
        await message.answer("🔒 Сначала /start и введи ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return
    await message.answer("Не понял. Используйте кнопки внизу.", reply_markup=get_main_keyboard())
