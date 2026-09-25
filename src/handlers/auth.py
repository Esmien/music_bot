"""Авторизация: ключ доступа, /logout и fallback-обработчик."""

import logging
import secrets

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from core.config import UIConfig, settings
from core.database import SessionLocal, User
from core.utils.error_notify import notify_owner
from core.utils.exceptions import AccessKeyNotSet
from domains.base.keyboards import get_main_keyboard
from domains.base.service import is_authorized
from fsm.registries.auth_registry import (
    discard_pending_auth,
    is_pending_auth,
    register_failed_key_attempt,
    reset_failed_key_attempts,
)
from fsm.registries.task_registry import get_active_task
from handlers.filters import IsPendingAuth, NotCommand

log = logging.getLogger(__name__)

router = Router()

# Защита от перебора ключа доступа: счётчик неудачных попыток на пользователя.
# Как и pending_auth, живёт в Redis и переживает перезапуск (см. auth_registry)
MAX_KEY_ATTEMPTS = 5


async def _require_auth(message: Message) -> bool:
    """Гарантирует авторизацию перед действием.

    Args:
        message: Входящее сообщение от пользователя.

    Returns:
        True, если пользователь авторизован; иначе отправляет
        подсказку и возвращает False.
    """
    uid = message.from_user.id

    # Пока пользователь на этапе ввода ключа или не авторизован — не пускаем к кнопкам.
    if await is_pending_auth(uid) or not await is_authorized(uid=uid):
        await message.answer(
            text="Требуется ключ доступа. Нажмите /start, чтобы ввести",
            reply_markup=ReplyKeyboardRemove(),
        )
        return False
    return True


async def _check_key_with_attempts(key: str, expected: str, uid: int) -> str | None:
    """Проверяет ключ и ведёт счётчик неудачных попыток.

    При успехе сбрасывает счётчик и возвращает None. При неверном ключе
    увеличивает счётчик; после MAX_KEY_ATTEMPTS удаляет пользователя
    из pending_auth, чтобы он начал с /start.

    Args:
        key: Ключ, введённый пользователем (уже strip'нутый).
        expected: Ожидаемый ключ из config.BOT_ACCESS_KEY.
        uid: Telegram user_id — ключ счётчика и pending_auth.

    Returns:
        None, если ключ верный. Иначе — текст ошибки для пользователя.
    """
    # Позиционно: compare_digest — C-функция, именованные аргументы не принимает.
    if secrets.compare_digest(key.encode("utf-8"), expected.encode("utf-8")):
        await reset_failed_key_attempts(uid=uid)
        return None

    attempts = await register_failed_key_attempt(uid=uid)
    if attempts >= MAX_KEY_ATTEMPTS:
        await discard_pending_auth(uid=uid)
        await reset_failed_key_attempts(uid=uid)
        log.warning("Access key attempts exhausted (user=%s)", uid)
        return "❌ Слишком много неверных попыток. Отправьте /start, чтобы начать заново."

    return "❌ Неверный ключ доступа."


async def _mark_user_authorized(uid: int) -> None:
    """Создаёт или обновляет User с is_authorized=True.

    Идемпотентна: существующему пользователю проставляет флаг, нового
    создаёт. IntegrityError на flush отлавливается на случай
    параллельной вставки того же tg_id — тогда запись перечитывается.

    Args:
        uid: Telegram user_id.
    """
    async with SessionLocal() as session:
        db_user = await session.get(User, uid)

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


@router.message(Command("logout"))
@router.message(F.text == UIConfig.LOGOUT_BUTTON)
async def cmd_logout(message: Message, state: FSMContext):
    """Выход: снимает авторизацию в БД и очищает состояние ожидания.

    Отзывает доступ, удаляет пользователя из pending_auth и сбрасывает FSM.
    Затем отменяет активную генерацию, если она запущена.

    Args:
        message: Входящее сообщение (команда /logout или кнопка выхода).
        state: FSM-контекст текущего пользователя.
    """
    uid = message.from_user.id
    try:
        async with SessionLocal() as session:
            result = await session.execute(select(User).where(User.tg_id == uid))
            db_user = result.scalar_one_or_none()
            if db_user:
                db_user.is_authorized = False
                session.add(db_user)
                await session.commit()
    except SQLAlchemyError as error:
        log.warning("User %s failed to logout (DB error)", uid)
        await notify_owner(
            bot=message.bot,
            context=f"Logout упал (user={uid}, username={message.from_user.username!r})",
            err=error,
        )
        await message.answer(text="Не удалось выйти. Генерация продолжается.")
        return

    task = get_active_task(uid)
    if task and not task.done():
        task.cancel()
    await discard_pending_auth(uid=uid)
    await state.clear()

    log.info("User %s logged out", uid)
    await message.answer(text="👋 Вы вышли. /start чтобы войти снова.", reply_markup=ReplyKeyboardRemove())


@router.message(F.text, NotCommand(), IsPendingAuth())
async def handle_key(message: Message):
    """Обрабатывает ключ доступа от неавторизованного пользователя.

    Удаляет сообщение с ключом, чтобы он не остался в истории чата.
    Ключ сравнивается с BOT_ACCESS_KEY через secrets.compare_digest.
    После MAX_KEY_ATTEMPTS неверных попыток пользователь выбывает
    из pending_auth и должен начать с /start.

    Args:
        message: Входящее сообщение с ключом доступа.
    """
    uid = message.from_user.id

    try:
        await message.delete()
    except TelegramAPIError:
        log.warning("Failed to delete message with access key (user=%s)", uid)

    expected_key = settings.bot.BOT_ACCESS_KEY
    if not expected_key:
        log.error("BOT_ACCESS_KEY is not set — authorization is impossible")
        await notify_owner(
            bot=message.bot,
            context="Не настроен ключ входа, необходимо проверить.",
            err=AccessKeyNotSet("BOT_ACCESS_KEY is empty"),
        )
        await message.answer(text="⚠️ Бот не настроен. Владелец уже уведомлен.")
        return

    error_text = await _check_key_with_attempts(key=message.text.strip(), expected=expected_key, uid=uid)
    if error_text is not None:
        await message.answer(text=error_text)
        return

    await _mark_user_authorized(uid=uid)
    await discard_pending_auth(uid=uid)
    await message.answer(text="✅ Вы успешно авторизованы!", reply_markup=get_main_keyboard())


@router.message(F.text, NotCommand())
async def fallback(message: Message):
    """Fallback для нераспознанных текстовых сообщений.

    Ожидающих ввод ключа пропускает, неавторизованным напоминает про
    /start, авторизованным — про кнопки управления.

    Args:
        message: Входящее текстовое сообщение.
    """
    uid = message.from_user.id

    if await is_pending_auth(uid):
        return

    if not await is_authorized(uid=uid):
        await message.answer(
            text="🔒 Сначала /start и введите ключ доступа.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await message.answer(text="Не понял. Используйте кнопки внизу.", reply_markup=get_main_keyboard())
