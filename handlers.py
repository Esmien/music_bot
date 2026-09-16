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

# Максимальная длина промпта и названия для защиты от перегруза модели/БД
MAX_PROMPT_LEN = 800
MAX_TITLE_LEN = 100

# Ожидающие пользователи временно "живут" в памяти (in-memory множество ID)
pending_auth: set[int] = set()


class GenerationStates(StatesGroup):
    """Состояния конечного автомата (FSM) для процесса генерации песни.

    Attributes:
        waiting_for_prompt: Состояние ожидания ввода текстового описания (промпта).
        waiting_for_title: Состояние ожидания ввода названия будущей песни.
    """
    waiting_for_prompt = State()
    waiting_for_title = State()


class IsPendingAuth(Filter):
    """Кастомный фильтр для проверки, ожидает ли пользователь авторизации.

    Используется вместо лямбда-фильтра, чтобы корректно обрабатывать
    служебные апдейты без поля from_user (например, некоторые типы чатов).
    """

    async def __call__(self, message: Message) -> bool:
        """Проверяет, что сообщение от пользователя и он в списке ожидающих.

        Args:
            message: Входящее сообщение от Telegram.

        Returns:
            True, если пользователь авторизуется (ждет ввод ключа), иначе False.
        """
        return message.from_user is not None and message.from_user.id in pending_auth


class NotCommand(Filter):
    """Фильтр для отсева команд (сообщений, начинающихся с '/')."""

    async def __call__(self, message: Message) -> bool:
        """Проверяет, что текст сообщения не является командой боту.

        Args:
            message: Входящее сообщение от Telegram.

        Returns:
            True, если текст не начинается с '/', иначе False.
        """
        text = message.text or ""
        return not text.startswith("/")


async def is_authorized(uid: int) -> bool:
    """Проверяет, авторизован ли пользователь в базе данных.

    Args:
        uid: Telegram ID пользователя.

    Returns:
        True, если пользователь найден и имеет флаг is_authorized, иначе False.
    """
    async with SessionLocal() as session:
        # Выполняем запрос к БД для поиска пользователя по tg_id
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        return bool(db_user and db_user.is_authorized)


async def _require_auth(message: Message) -> bool:
    """Гарантирует, что пользователь авторизован перед выполнением действия.

    Если пользователь не авторизован или ожидает ввода ключа, отправляет
    соответствующее уведомление и убирает клавиатуру.

    Args:
        message: Сообщение от пользователя, инициировавшее действие.

    Returns:
        True, если доступ разрешён (авторизован), иначе False.
    """
    uid = message.from_user.id
    if uid in pending_auth:
        # Пользователь ещё не ввёл ключ доступа
        await message.answer("Сначала отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return False
    if not await is_authorized(uid):
        # Пользователь не авторизован в БД
        await message.answer("Сначала отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove())
        return False
    return True


def get_main_keyboard():
    """Создаёт основную клавиатуру с главными действиями бота.

    Returns:
        Объект разметки клавиатуры (ReplyKeyboardMarkup) с кнопками генерации,
        проверки кредитов и выхода.
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text="🎵 Сгенерировать")
    builder.button(text="💳 Кредиты")
    builder.button(text="🚪 Выйти")
    builder.adjust(2)  # Две кнопки в ряд, третья на новой строке
    return builder.as_markup(resize_keyboard=True)


def get_cancel_keyboard():
    """Создаёт клавиатуру с единственной кнопкой отмены операции.

    Returns:
        Объект разметки клавиатуры с кнопкой "❌ Отмена".
    """
    builder = ReplyKeyboardBuilder()
    builder.button(text="❌ Отмена")
    return builder.as_markup(resize_keyboard=True)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start.

    Сбрасывает состояние FSM, проверяет статус авторизации пользователя
    и либо приветствует с основной клавиатурой, либо запрашивает ключ доступа.

    Args:
        message: Сообщение с командой /start.
        state: Контекст состояния FSM для текущего пользователя.
    """
    await state.clear()  # Очищаем возможные зависшие состояния
    uid = message.from_user.id
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.tg_id == uid))
        db_user = result.scalar_one_or_none()
        if db_user and db_user.is_authorized:
            # Уже авторизован — показываем главное меню
            await message.answer(
                "👋 Привет! Я бот для генерации песен.\nИспользуйте кнопки ниже для управления.",
                reply_markup=get_main_keyboard(),
            )
        else:
            # Не авторизован — добавляем в ожидающие и просим ключ
            pending_auth.add(uid)
            await message.answer(
                "👋 Привет! Для использования бота отправьте ключ доступа.", reply_markup=ReplyKeyboardRemove()
            )


@router.message(Command("logout"))
@router.message(F.text == "🚪 Выйти")
async def cmd_logout(message: Message, state: FSMContext):
    """Обработчик выхода из системы (команда /logout или кнопка).

    Удаляет пользователя из ожидающих, снимает флаг авторизации в БД
    и уведомляет об успешном выходе.

    Args:
        message: Сообщение от пользователя.
        state: Контекст состояния FSM.
    """
    await state.clear()
    uid = message.from_user.id
    pending_auth.discard(uid)  # Убираем из списка ожидающих авторизации
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
    """Обработчик проверки оставшихся кредитов OpenRouter.

    Требует авторизации, делает запрос к API OpenRouter и переводит
    денежные лимиты в примерное количество песен (исходя из цены ~0.08 за трек).

    Args:
        message: Сообщение от пользователя.
        state: Контекст состояния FSM.
    """
    if not await _require_auth(message):
        return  # Прерываем, если нет авторизации
    await state.clear()
    if not config.OPENROUTER_API_KEY:
        await message.answer("⚠️ Бот не настроен (API), обратитесь к автору.")
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
                """Конвертирует сумму в кол-во песен (по 0.08 у.е. за штуку)."""
                return int(value / 0.08) if isinstance(value, (int, float)) else msg

            total_songs = _songs_counter(value=total, msg="Без лимита")
            used_songs = _songs_counter(value=used, msg="0")
            remaining_songs = _songs_counter(
                value=remaining, msg="Пока не кончится бабосик или Влад не вспомнит про лимит 😁"
            )

            await message.answer(
                f"💳 Баланс песен:\n"
                f"Всего доступно генераций: {total_songs}\n"
                f"Сгенерировано композиций: {used_songs}\n"
                f"Доступное количество генераций: {remaining_songs}",
                reply_markup=get_main_keyboard(),
            )
    except Exception as e:
        log.exception("Credits check failed")
        await message.answer(f"❌ Не получилось проверить остатки: {e}")


@router.message(F.text == "🎵 Сгенерировать")
async def cmd_generate(message: Message, state: FSMContext):
    """Запускает процесс генерации песни.

    Проверяет авторизацию и переводит пользователя в состояние ожидания
    текстового описания (промпта), показывая клавиатуру с отменой.

    Args:
        message: Сообщение от пользователя.
        state: Контекст состояния FSM.
    """
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

    template = ("Жанр: \n"
                "Настроение: \n"
                "Инструменты: \n"
                "Темп и ритм: \n"
                "Голос: \n"
                "Текст песни: ")


    await message.answer(text=text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await message.answer(f"<code>{template}</code>", parse_mode="HTML")

    await state.set_state(GenerationStates.waiting_for_prompt)


@router.message(GenerationStates.waiting_for_prompt, F.text == "❌ Отмена")
@router.message(GenerationStates.waiting_for_title, F.text == "❌ Отмена")
async def cmd_cancel_generation(message: Message, state: FSMContext):
    """Отменяет текущий процесс генерации по нажатию кнопки "❌ Отмена".

    Сбрасывает состояние FSM и возвращает пользователя в главное меню.

    Args:
        message: Сообщение с текстом "❌ Отмена".
        state: Контекст состояния FSM.
    """
    await state.clear()
    await message.answer("❌ Генерация отменена.", reply_markup=get_main_keyboard())


@router.message(GenerationStates.waiting_for_prompt, F.text)
async def handle_prompt(message: Message, state: FSMContext):
    """Принимает и валидирует текстовое описание (промпт) для будущей песни.

    Проверяет непустоту и длину, сохраняет в состояние и запрашивает название.

    Args:
        message: Сообщение с текстом промпта.
        state: Контекст состояния FSM.
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

    await state.update_data(prompt=structured_prompt)  # Сохраняем промпт в FSM
    await state.set_state(GenerationStates.waiting_for_title)
    await message.answer("🎤 Введите название песни:", reply_markup=get_cancel_keyboard())


@router.message(GenerationStates.waiting_for_title, F.text)
async def handle_title(message: Message, state: FSMContext):
    """Принимает название, генерирует песню и отправляет аудиофайл пользователю.

    Валидирует название, извлекает промпт из состояния, вызывает сервис
    генерации (реальный или мок) и отправляет результат.

    Args:
        message: Сообщение с названием песни.
        state: Контекст состояния FSM.
    """
    title = message.text.strip()
    if not title:
        await message.answer("Пожалуйста, введите непустое название.")
        return
    if len(title) > MAX_TITLE_LEN:
        await message.answer(f"Слишком длинное название. Максимум {MAX_TITLE_LEN} символов.")
        return

    data = await state.get_data()
    prompt = data.get("prompt", "")
    await state.clear()  # Освобождаем FSM, так как все данные собраны

    bot = message.bot
    # Показываем индикатор "загрузка голосового" и статус
    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_VOICE)
    status = await message.answer("🎼 Генерирую… Это может занять до 1–2 минут.")

    try:
        if config.MOCK_MODE:
            # Режим заглушки для тестов без обращения к внешним API
            await asyncio.sleep(3)
            audio_bytes = generation.load_mock_audio()
        else:
            audio_bytes = await generation.generate_song_real(prompt)
    except Exception as e:
        log.exception("Generation failed")
        await status.edit_text(f"❌ Не получилось: {e}")
        await message.answer("Выберите действие:", reply_markup=get_main_keyboard())
        return

    # Очищаем имя файла от спецсимволов, ограничиваем длину
    safe_title = "".join(c if c.isalnum() or c in "_-." else "_" for c in title)[:80] or "song"
    file = BufferedInputFile(audio_bytes, filename=f"{safe_title}.mp3")
    await message.answer_audio(file, caption="🎵 Готово!", reply_markup=get_main_keyboard())
    await status.delete()  # Убираем сообщение о генерации


@router.message(F.text, NotCommand(), IsPendingAuth())
async def handle_key(message: Message):
    """Обрабатывает ввод ключа доступа от неавторизованного пользователя.

    Сравнивает введённый ключ с эталонным (безопасно через compare_digest),
    создаёт или обновляет запись пользователя в БД и выдаёт доступ.

    Args:
        message: Сообщение с предполагаемым ключом доступа.
    """
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
        # Используем compare_digest против timing-атак
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
                # На случай параллельной вставки того же пользователя
                await session.rollback()
                result = await session.execute(select(User).where(User.tg_id == uid))
                db_user = result.scalar_one_or_none()
                if db_user:
                    db_user.is_authorized = True
        await session.commit()

    pending_auth.discard(uid)  # Убираем из ожидания авторизации
    await message.answer("✅ Вы успешно авторизованы!", reply_markup=get_main_keyboard())


@router.message(F.text, NotCommand())
async def fallback(message: Message):
    """Fallback-обработчик для любых нераспознанных текстовых сообщений.

    Если пользователь не авторизован — просит ключ, иначе предлагает
    воспользоваться кнопками меню.

    Args:
        message: Любое текстовое сообщение, не попавшее в другие хендлеры.
    """
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
