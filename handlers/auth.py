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
from database.models import User

from .filters import IsPendingAuth, NotCommand
from .keyboards import get_main_keyboard
from .state import active_tasks, pending_auth

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
        await message.answer(text="Сначала отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return False
    return True


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """/start: приветствие и проверка статуса авторизации.

    Точка входа для нового пользователя. Авторизованным отправляет
    приветствие с основной клавиатурой, неавторизованным — предложение
    отправить ключ доступа, попутно добавляя их в pending_auth: дальше
    ввод ключа перехватит handle_key через фильтр IsPendingAuth.

    Args:
        message: Входящее сообщение с командой /start.
        state: FSM-контекст; очищается, чтобы сбросить незавершённые сценарии.
    """
    await state.clear()
    uid = message.from_user.id
    if await is_authorized(uid):
        await message.answer(
            text="👋 Привет! Я бот для генерации песен.\nИспользуйте кнопки ниже для управления.",
            reply_markup=get_main_keyboard(),
        )
    else:
        pending_auth.add(uid)
        await message.answer(
            text="👋 Привет! Для использования бота отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove()
        )


@router.message(Command("logout"))
@router.message(F.text == "🚪 Выйти")
async def cmd_logout(message: Message, state: FSMContext):
    """Выход: снимает авторизацию в БД и очищает состояние ожидания.

    Отзывает доступ (is_authorized=False в БД), удаляет пользователя
    из pending_auth и сбрасывает FSM. Параллельно гасит активную
    генерацию, если она запущена, — иначе после выхода пользователю
    всё равно пришла бы готовая песня.

    Args:
        message: Входящее сообщение (команда /logout или кнопка «🚪 Выйти»).
        state: FSM-контекст текущего пользователя.
    """
    # Гасим живую генерацию, если она есть: иначе после logout пользователю
    # всё равно прилетит песня.
    task = active_tasks.get(message.from_user.id)
    if task is not None and not task.done():
        task.cancel()
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
    await message.answer(text="👋 Вы вышли. /start чтобы войти снова.", reply_markup=ReplyKeyboardRemove())


@router.message(F.text, NotCommand(), IsPendingAuth())
async def handle_key(message: Message):
    """Обработка ключа доступа от неавторизованного пользователя.

    Сразу удаляет сообщение с ключом, чтобы он не остался в истории чата.
    Ключ сравнивается с BOT_ACCESS_KEY через secrets.compare_digest
    (защита от timing-атак). После MAX_KEY_ATTEMPTS неверных попыток
    пользователь выбывает из pending_auth и должен начать с /start.
    При успехе создаёт либо авторизует запись User в БД и снимает
    статус ожидания ключа.

    Args:
        message: Входящее сообщение с ключом доступа.
    """
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
        await message.answer(text="⚠️ Бот не настроен. Сообщите владельцу.")
        return

    # Позиционно: compare_digest — C-функция, именованные аргументы не принимает
    if not secrets.compare_digest(key.encode("utf-8"), expected_key.encode("utf-8")):
        attempts = failed_key_attempts.get(uid, 0) + 1
        failed_key_attempts[uid] = attempts
        if attempts >= MAX_KEY_ATTEMPTS:
            pending_auth.discard(uid)
            failed_key_attempts.pop(uid, None)
            log.warning("Исчерпаны попытки ввода ключа (user=%s)", uid)
            await message.answer(text="❌ Слишком много неверных попыток. Отправьте /start, чтобы начать заново.")
            return
        await message.answer(text="❌ Неверный ключ доступа.")
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
    await message.answer(text="✅ Вы успешно авторизованы!", reply_markup=get_main_keyboard())


@router.message(F.text, NotCommand())
async def fallback(message: Message):
    """Fallback для нераспознанных текстовых сообщений.

    Регистрируется последним, поэтому срабатывает, только если текст
    не подошёл ни одному более специфичному хендлеру. Сообщения от
    ожидающих ввод ключа пользователей пропускает (их обработает
    handle_key), неавторизованным напоминает про /start, авторизованным —
    про управление кнопками.

    Args:
        message: Входящее текстовое сообщение.
    """
    uid = message.from_user.id
    if uid in pending_auth:
        return  # пусть обработает handle_key
    if not await is_authorized(uid):
        await message.answer(text="🔒 Сначала /start и введи ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return
    await message.answer(text="Не понял. Используйте кнопки внизу.", reply_markup=get_main_keyboard())
