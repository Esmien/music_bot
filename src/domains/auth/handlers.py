"""Telegram-обработчики домена авторизации."""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core.config import UIConfig, settings
from core.database import User
from core.database.engine import get_session
from core.utils.error_notify import notify_owner
from core.utils.exceptions import AccessKeyNotSet
from domains.auth.service import (
    check_key_with_attempts,
    discard_pending_auth,
    is_authorized,
    is_pending_auth,
    mark_user_authorized,
)
from domains.base.keyboards import get_main_keyboard
from domains.generation.registries.task_registry import get_active_task

log = logging.getLogger(__name__)

router = Router(name="auth")


class IsPendingAuth(Filter):
    """Фильтр: пользователь ожидает ввода ключа доступа."""

    async def __call__(self, message: Message) -> bool:
        """Проверяет наличие пользователя в реестре ожидания авторизации.

        Args:
            message: Входящее сообщение.

        Returns:
            True, если сообщение отправил пользователь, ожидающий ввода ключа.
        """
        return message.from_user is not None and await is_pending_auth(uid=message.from_user.id)


class NotCommand(Filter):
    """Фильтр: текст сообщения не является командой."""

    async def __call__(self, message: Message) -> bool:
        """Проверяет, что текст сообщения не начинается с '/'.

        Args:
            message: Входящее сообщение.

        Returns:
            True, если сообщение не является командой.
        """
        text = message.text or ""
        return not text.startswith("/")


async def _require_auth(message: Message) -> bool:
    """Гарантирует авторизацию перед действием.

    Args:
        message: Входящее сообщение от пользователя.

    Returns:
        True, если пользователь авторизован; иначе отправляет
        подсказку и возвращает False.
    """
    uid = message.from_user.id
    if await is_pending_auth(uid=uid) or not await is_authorized(uid=uid):
        await message.answer(
            text="Требуется ключ доступа. Нажмите /start, чтобы ввести",
            reply_markup=ReplyKeyboardRemove(),
        )
        return False
    return True


async def _logout_user(uid: int) -> None:
    """Снимает авторизацию пользователя в базе данных.

    Args:
        uid: Telegram user_id.
    """
    async with get_session() as session:
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        if db_user:
            db_user.is_authorized = False
            session.add(db_user)
            await session.commit()


@router.message(Command("logout"))
@router.message(F.text == UIConfig.LOGOUT_BUTTON)
async def cmd_logout(message: Message, state: FSMContext) -> None:
    """Отзывает доступ, очищает состояние и отменяет активную генерацию.

    Args:
        message: Команда /logout или нажатие кнопки выхода.
        state: FSM-контекст текущего пользователя.
    """
    uid = message.from_user.id
    try:
        await _logout_user(uid=uid)
    except SQLAlchemyError as error:
        log.warning("User %s failed to logout (DB error)", uid)
        await notify_owner(
            bot=message.bot,
            context=f"Logout упал (user={uid}, username={message.from_user.username!r})",
            err=error,
        )
        await message.answer(text="Не удалось выйти. Генерация продолжается.")
        return

    task = get_active_task(uid=uid)
    if task and not task.done():
        task.cancel()
    await discard_pending_auth(uid=uid)
    await state.clear()

    log.info("User %s logged out", uid)
    await message.answer(text="👋 Вы вышли. /start чтобы войти снова.", reply_markup=ReplyKeyboardRemove())


@router.message(F.text, NotCommand(), IsPendingAuth())
async def handle_key(message: Message) -> None:
    """Обрабатывает ключ доступа от неавторизованного пользователя.

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

    error_text = await check_key_with_attempts(key=message.text.strip(), expected=expected_key, uid=uid)
    if error_text is not None:
        if "Слишком много" in error_text:
            log.warning("Access key attempts exhausted (user=%s)", uid)
        await message.answer(text=error_text)
        return

    await mark_user_authorized(uid=uid)
    await discard_pending_auth(uid=uid)
    await message.answer(text="✅ Вы успешно авторизованы!", reply_markup=get_main_keyboard())


@router.message(F.text, NotCommand())
async def fallback(message: Message) -> None:
    """Отвечает на нераспознанные текстовые сообщения.

    Args:
        message: Входящее текстовое сообщение.
    """
    uid = message.from_user.id

    if await is_pending_auth(uid=uid):
        return

    if not await is_authorized(uid=uid):
        await message.answer(
            text="🔒 Сначала /start и введите ключ доступа.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await message.answer(text="Не понял. Используйте кнопки внизу.", reply_markup=get_main_keyboard())
