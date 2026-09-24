"""Авторизация: ключ доступа, /start, /logout, fallback-обработчик."""

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
from core.database.models import GenerationFeedback
from core.utils.error_notify import notify_owner
from core.utils.exceptions import AccessKeyNotSet
from fsm.registries.auth_registry import (
    add_pending_auth,
    discard_pending_auth,
    is_pending_auth,
    register_failed_key_attempt,
    reset_failed_key_attempts,
)
from fsm.registries.task_registry import get_active_task
from handlers.filters import IsPendingAuth, NotCommand
from keyboards.default_keyboards import get_main_keyboard

log = logging.getLogger(__name__)

router = Router()

# Защита от перебора ключа доступа: счётчик неудачных попыток на пользователя.
# Как и pending_auth, живёт в Redis и переживает перезапуск (см. auth_registry)
MAX_KEY_ATTEMPTS = 5


async def is_authorized(uid: int) -> bool:
    """Проверяет по БД, авторизован ли пользователь.

    Args:
        uid: Telegram user_id.

    Returns:
        True, если пользователь найден и is_authorized=True.
    """
    async with SessionLocal() as session:
        db_user = await session.get(User, uid)

        return bool(db_user and db_user.is_authorized)


async def _get_last_generated_title(uid: int) -> str | None:
    """Возвращает название последней сгенерированной песни пользователя.

    Args:
        uid: Telegram user_id.

    Returns:
        Название последней генерации или None, если её нет.
    """
    async with SessionLocal() as session:
        result = await session.execute(
            select(GenerationFeedback.title)
            .where(GenerationFeedback.user_id == uid, GenerationFeedback.title.isnot(None))
            .order_by(GenerationFeedback.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _require_auth(message: Message) -> bool:
    """Гарантирует авторизацию перед действием.

    Args:
        message: Входящее сообщение от пользователя.

    Returns:
        True, если пользователь авторизован; иначе отправляет
        подсказку и возвращает False.
    """
    uid = message.from_user.id

    # пока пользователь на этапе ввода ключа или не авторизован - не пускаем к кнопкам и требуем ключ
    if await is_pending_auth(uid) or not await is_authorized(uid):
        await message.answer(
            text="Требуется ключ доступа. Нажмите /start, чтобы ввести", reply_markup=ReplyKeyboardRemove()
        )
        return False
    return True


async def _check_key_with_attempts(key: str, expected: str, uid: int) -> str | None:
    """Проверяет ключ и ведёт счётчик неудачных попыток.

    При успехе сбрасывает счётчик и возвращает None. При неверном ключе
    увеличивает счётчик; после MAX_KEY_ATTEMPTS выкидывает пользователя
    из pending_auth, чтобы он начал с /start.

    Args:
        key: Ключ, введённый пользователем (уже strip'нутый).
        expected: Ожидаемый ключ из config.BOT_ACCESS_KEY.
        uid: Telegram user_id — ключ счётчика и pending_auth.

    Returns:
        None, если ключ верный. Иначе — текст ошибки для пользователя.
    """
    # Позиционно: compare_digest — C-функция, именованные аргументы не принимает
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
    параллельной вставки того же tg_id — тогда просто перечитываем
    запись и проставляем флаг ей.

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


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """/start: приветствие и проверка статуса авторизации.

    Точка входа. Авторизованным показывает персонализированное приветствие:
    вернувшемуся (есть последняя генерация) — «С возвращением, {имя}» с
    названием последнего трека, новому — обычное приветствие. Неавторизованным —
    предложение отправить ключ доступа, попутно добавляя их в pending_auth:
    дальше ввод ключа перехватит handle_key через фильтр IsPendingAuth.

    Args:
        message: Входящее сообщение с командой /start.
        state: FSM-контекст; очищается, чтобы сбросить незавершённые сценарии.
    """
    # чистим весь стейт при /start. команда работает как reset
    await state.clear()
    uid = message.from_user.id

    # если у пользователя is_authorized=True - отправляем на стартовый экран
    if await is_authorized(uid):
        # убираем из реестра ожидания ключа, не тратим память
        await discard_pending_auth(uid=uid)
        last_title = await _get_last_generated_title(uid=uid)
        if last_title:
            tg_name = message.from_user.first_name or message.from_user.username or "друг"
            await message.answer(
                text=(
                    f"👋 С возвращением, {tg_name}!\n"
                    f"Последняя генерация: {last_title}\n"
                    "Используйте кнопки ниже для управления."
                ),
                reply_markup=get_main_keyboard(),
            )
        else:
            await message.answer(
                text="👋 Привет! Я бот для генерации песен.\nИспользуйте кнопки ниже для управления.",
                reply_markup=get_main_keyboard(),
            )
    else:
        # пользователь не залогинен, добавляем в реестр "ожидает ключа"
        await add_pending_auth(uid=uid)
        await message.answer(
            text="👋 Привет! Для использования бота отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove()
        )


@router.message(Command("logout"))
@router.message(F.text == UIConfig.LOGOUT_BUTTON)
async def cmd_logout(message: Message, state: FSMContext):
    """Выход: снимает авторизацию в БД и очищает состояние ожидания.

    Отзывает доступ (is_authorized=False в БД), удаляет пользователя
    из pending_auth и сбрасывает FSM. Затем гасит активную генерацию,
    если она запущена, — иначе после выхода пользователю
    всё равно пришла бы готовая песня.

    Args:
        message: Входящее сообщение (команда /logout или кнопка «🚪 Выйти»).
        state: FSM-контекст текущего пользователя.
    """
    uid = message.from_user.id
    # идемпотентно меняем статус юзера на неактивного
    try:
        async with SessionLocal() as session:
            result = await session.execute(select(User).where(User.tg_id == uid))
            db_user = result.scalar_one_or_none()
            if db_user:
                db_user.is_authorized = False
                session.add(db_user)
                await session.commit()
    except SQLAlchemyError as e:
        log.warning("User %s failed to logout (DB error)", uid)
        await notify_owner(
            bot=message.bot, context=f"Logout упал (user={uid}, username={message.from_user.username!r})", err=e
        )
        await message.answer(text="Не удалось выйти. Генерация продолжается.")
        return

    # Гасим живую генерацию, если она есть: иначе после logout пользователю
    # всё равно прилетит песня.
    task = get_active_task(uid)
    if task and not task.done():
        task.cancel()
    # убираем из реестра ожидающих ключ
    await discard_pending_auth(uid=uid)
    await state.clear()

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

    # Удаляем сообщение с ключом, чтобы он не оставался в истории чата
    try:
        await message.delete()
    except TelegramAPIError:
        log.warning("Failed to delete message with access key (user=%s)", uid)

    # Уведомляем владельца о сбое в настройке и не пускаем дальше (иначе вход открыт для всех)
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

    # Либо сообщение о неверном ключе, либо об исчерпании попыток. В любом случае не пускаем
    error_text = await _check_key_with_attempts(key=message.text.strip(), expected=expected_key, uid=uid)
    if error_text is not None:
        await message.answer(text=error_text)
        return

    # Авторизовываем, убираем из реестра ожидающих
    await _mark_user_authorized(uid=uid)
    await discard_pending_auth(uid=uid)
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

    if await is_pending_auth(uid):
        return  # пусть обработает handle_key

    if not await is_authorized(uid):
        await message.answer(text="🔒 Сначала /start и введите ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return
    # Срабатывает для пользователей без стейта (промпт/название)
    await message.answer(text="Не понял. Используйте кнопки внизу.", reply_markup=get_main_keyboard())
