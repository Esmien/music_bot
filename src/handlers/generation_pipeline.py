"""Хендлеровый слой конвейера генерации: FSM, статусы, отмена и сбои.

«Железная» логика запуска (пер-пользовательские локи, троттлинг
прогресса, демо/реальный режим) живет в services/pipeline.py; здесь —
только работа с Telegram, FSM-состоянием и реестром active_tasks.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from uuid import uuid4

from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from core.database import SessionLocal
from core.database.models import GenerationFeedback
from core.utils.error_notify import notify_owner
from fsm.registries.task_registry import register_active_task, unregister_active_task
from keyboards.default_keyboards import get_main_keyboard
from services.generation import ProgressCallback
from services.pipeline import make_throttled_progress, run_generation, user_generation_lock

log = logging.getLogger(__name__)


async def _is_actual_gen(state: FSMContext, gen_id: str) -> bool:
    """Проверяет, актуальна ли генерация в стейте

    Args:
        state: состояние FSM пользователя
        gen_id: id генерации из контекста

    Returns:
        True, если актуальна
    """
    return (await state.get_data()).get("gen_id") == gen_id


@dataclass
class GenerationContext:
    """Контекст одного запуска генерации.

    Атрибуты:
        message: Сообщение, от имени которого шлются статусы и аудио.
        state: FSM-контекст пользователя.
        prompt: Подготовленное описание песни.
        title: Название трека (используется в имени файла).
        user_id: Telegram user_id пользователя.
        task: Фоновая задача, в которой крутится генерация.
        gen_id: Маркер запуска; защищает от затирания нового FSM-состояния
            поздно завершившейся старой генерацией.
    """

    message: Message
    state: FSMContext
    prompt: str
    title: str
    user_id: int
    task: asyncio.Task
    gen_id: str = ""


async def generate_and_send(message: Message, state: FSMContext, prompt: str, title: str, user_id: int) -> None:
    """Генерирует аудио и отправляет его в чат.

    Запускается как фоновая задача из handle_title и retry_generation.
    Под локом проверяет и выставляет флаг generating в FSM (защита от
    параллельных запусков) и регистрирует себя в active_tasks, чтобы
    генерацию можно было погасить из cmd_cancel_generation / cmd_logout.
    По ходу дела правит сообщение-статус с прогресс-баром (не чаще
    PROGRESS_EDIT_INTERVAL). При ошибке оставляет prompt и title в FSM
    и вешает кнопку повтора; при отмене убирает сообщение прогресса и
    пробрасывает CancelledError. Успех завершается отправкой аудио и
    очисткой FSM — но только если генерация всё ещё актуальна.

    Args:
        message: Сообщение, от имени которого шлются статусы и аудио.
        state: FSM-контекст пользователя.
        prompt: Подготовленное описание песни.
        title: Название трека (используется в имени файла).
        user_id: Telegram user_id пользователя.
    """
    # current_task() возвращает Optional[Task]; в корутине он фактически не None,
    # но type-checker требует явной проверки. Явный raise вместо assert:
    # под python -O assert вырезается, а это боевой инвариант
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("generate_and_send must run inside an asyncio Task")

    # Собираем весь контекст генерации в кучу
    gen_context = GenerationContext(
        message=message,
        state=state,
        prompt=prompt,
        title=title,
        user_id=user_id,
        task=task,
    )
    try:
        # Слот занят другой генерацией - не запускаем новую
        if not await _acquire_slot(gen_context=gen_context):
            return

        # Запускаем визуальный процесс генерации для пользователя
        status = await _start_status(gen_context=gen_context)
        on_progress = _make_progress_reporter(status=status)

        try:
            audio_bytes = await run_generation(prompt=gen_context.prompt, on_progress=on_progress)
        except asyncio.CancelledError:
            await _cleanup_cancelled(gen_context=gen_context, status=status)
            raise
        except Exception as error:
            await _handle_failure(gen_context=gen_context, status=status, error=error)
            return
        await _deliver_result(gen_context=gen_context, status=status, audio_bytes=audio_bytes)
    finally:
        await _release_slot(gen_context=gen_context)


async def _acquire_slot(gen_context: GenerationContext) -> bool:
    """Атомарно занимает слот генерации пользователя.

    Под пер-пользовательским локом проверяет флаг generating и выставляет
    его вместе с маркером gen_id; регистрирует задачу в active_tasks —
    «активной» становится только та генерация, что прошла проверку, —
    иначе cancel() погасил бы задачу, которая лишь ждёт лок, а не
    реально генерирует.

    Args:
        gen_context: Контекст запуска генерации.

    Returns:
        True, если слот занят и генерацию можно запускать.
    """
    async with user_generation_lock(user_id=gen_context.user_id):
        # Проверка, занят ли слот. Если занят, не даем запустить новую
        if (await gen_context.state.get_data()).get("generating"):
            await gen_context.message.answer(text="⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
            # Сообщаем, что слот занят, генерировать нельзя
            return False

        # Присваиваем маркер конкретной генерации
        gen_context.gen_id = uuid4().hex
        # Устанавливаем в FSM пользователя
        # статус "генерируется" и маркер самой генерации, занимая слот
        await gen_context.state.update_data(generating=True, gen_id=gen_context.gen_id)
        # Регистрируем в едином реестре текущих задач
        await register_active_task(uid=gen_context.user_id, task=gen_context.task)
        return True


async def _start_status(gen_context: GenerationContext) -> Message:
    """Показывает «бот отправляет файл…» и создаёт сообщение-статус с прогрессом."""
    await gen_context.message.bot.send_chat_action(
        chat_id=gen_context.message.chat.id, action=ChatAction.UPLOAD_DOCUMENT
    )

    return await gen_context.message.answer(text="🎼 Генерирую… Это может занять до 1–2 минут.")


def _make_progress_reporter(status: Message) -> ProgressCallback:
    """Возвращает колбэк on_progress, троттлящий правки статуса.

    Троттлинг и сборку текста делает services.pipeline; здесь —
    только «отрисовка» через edit_text конкретного сообщения.

    Args:
        status: Сообщение-статус, которое редактируется по мере прогресса.

    Returns:
        Колбэк on_progress(stage, fraction) для сервиса генерации.
    """

    async def report(text: str) -> None:
        """Отрисовывает новый текст статуса, глотая сбои правки.

        Args:
            text: Готовый текст статуса с прогресс-баром.
        """
        with contextlib.suppress(Exception):
            # Позиционно: заглушки edit_text в тестах объявлены как (new_text, **kwargs)
            await status.edit_text(text)

    return make_throttled_progress(report=report)


async def _cleanup_cancelled(gen_context: GenerationContext, status: Message) -> None:
    """Чистим экран пользователя после отмены генерации.
    Сама отмена приходит из cmd_cancel_generation / cmd_logout через task.cancel().

    Убираем «ползущее» сообщение прогресса.
    Флаг 'generating' обычно уже стёрт state.clear() в отменившем хендлере.
    Снимаем сами, только если gen_id в стейте еще актуален.
    Прилетевший CancelledError пропускаем дальше, его получит Task и
    отменит задачу

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
    """
    # Чистим экран пользователя от прогресс-бара
    with contextlib.suppress(Exception):
        await status.delete()

    # Снимаем флаг только если в FSM все еще текущая генерация
    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        await gen_context.state.update_data(generating=False)


async def _handle_failure(gen_context: GenerationContext, status: Message, error: Exception) -> None:
    """Обрабатывает сбой сервиса: уведомляет владельца и рисует кнопку повтора.

    Состояние НЕ чистим: prompt и title остались в FSM,
    повтор не требует сборки контекста еще раз.
    Флаг снимаем только если генерация всё ещё актуальна.
    Сбой самого уведомления не должен лишить пользователя кнопки ретрая.

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
        error: Исключение, упавшее из сервиса генерации.
    """
    with contextlib.suppress(Exception):
        await notify_owner(
            bot=gen_context.message.bot,
            context=f"Генерация упала (user={gen_context.user_id}, title={gen_context.title!r})",
            err=error,
        )

    # Защита только для флага: не глушим генерирующий флаг новой генерации.
    # Кнопка ретрая вешается всегда — контекст она возьмёт из FSM в момент нажатия.
    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        await gen_context.state.update_data(generating=False)
    retry_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="retry_generation")]]
    )

    # Даже если правка статуса не пройдёт — не роняем задачу, кнопка ретрая важнее
    with contextlib.suppress(Exception):
        await status.edit_text(
            "😔 Не получилось сгенерировать. Попробуйте ещё раз чуть позже.",
            reply_markup=retry_kb,
        )


async def _deliver_result(gen_context: GenerationContext, status: Message, audio_bytes: bytes) -> None:
    """Отправляет готовое аудио, сохраняет название и чистит состояние после успеха.

    Состояние чистим, только если это всё ещё актуальная генерация:
    иначе «поздний» успех после «❌ Отмена» затрёт состояние новой сессии.

    Args:
        gen_context: Контекст запуска генерации.
        status: Сообщение-статус с прогрессом.
        audio_bytes: Байты готового аудио.
    """
    if await _is_actual_gen(state=gen_context.state, gen_id=gen_context.gen_id):
        await gen_context.state.clear()

    # Санитайзинг названия песни, чтобы ТГ не сошел с ума от "левых" символов
    safe_title = "".join(c if c.isalnum() or c in "_-." else "_" for c in gen_context.title)[:80] or "song"

    # Сборка песни в файл, отправка пользователю и очистка экрана от прогресс-бара
    file = BufferedInputFile(file=audio_bytes, filename=f"{safe_title}.mp3")
    await gen_context.message.answer_audio(audio=file, caption="🎵 Готово!", reply_markup=get_main_keyboard())

    # Фиксируем название готовой песни — нужно для приветствия «С возвращением».
    await _persist_generated_title(gen_context=gen_context)

    # Поведенческая симметрия с _cleanup_cancelled: сбой удаления статуса
    # не должен ронять задачу уже после отправки аудио
    with contextlib.suppress(Exception):
        await status.delete()


async def _persist_generated_title(gen_context: GenerationContext) -> None:
    """Сохраняет название готовой песни в запись фидбека пользователя.

    Обновляет последнюю запись `GenerationFeedback`; если записи ещё нет,
    создаёт минимальную. Нужно для персонализированного приветствия на
    /start. Сбой БД не должен ронять задачу уже после отправки аудио.

    Args:
        gen_context: Контекст запуска генерации.
    """
    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(GenerationFeedback)
                .where(GenerationFeedback.user_id == gen_context.user_id)
                .order_by(GenerationFeedback.id.desc())
                .limit(1)
            )
            feedback = result.scalar_one_or_none()
            if feedback is not None:
                feedback.title = gen_context.title
            else:
                # DEVIATION: записи фидбека нет — создаём минимальную, чтобы
                # «С возвращением» работал и в сценариях без сохранения промпта.
                session.add(
                    GenerationFeedback(
                        user_id=gen_context.user_id,
                        initial_prompt=gen_context.prompt,
                        enriched_prompt=gen_context.prompt,
                        title=gen_context.title,
                    )
                )
            await session.commit()
    except Exception:
        log.error("Failed to persist generated title (user=%s)", gen_context.user_id, exc_info=True)


async def _release_slot(gen_context: GenerationContext) -> None:
    """Снимает регистрацию задачи в едином реестре активных генераций.

    Убираем только свою запись: за время генерации могла начаться новая
    (другая задача) — её запись не трогаем.

    Args:
        gen_context: Контекст запуска генерации.
    """
    await unregister_active_task(uid=gen_context.user_id, task=gen_context.task)
