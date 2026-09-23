"""Хендлеры точки входа генерации: кнопка «Сгенерировать» и повтор после сбоя.

Диалог генерации (описание → обогащение → аппрув → название) ведёт
сценарий обогащения — enricher_handlers; здесь остаются запуск сценария
кнопкой и перезапуск генерации после сбоя. Механика запуска (локи,
прогресс, отмена, обработка сбоев) вынесена в generation_pipeline.
"""

import contextlib

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from core.config import UIConfig
from fsm.enricher_fsm import PromptEnricherStates
from handlers.auth import _require_auth, is_authorized
from handlers.enricher_handlers import PROMPT_HINT, PROMPT_TEMPLATE
from handlers.generation_pipeline import generate_and_send
from keyboards.default_keyboards import get_cancel_keyboard

router = Router()

# Название по умолчанию для повтора, если title потерялся из FSM
_DEFAULT_TITLE = "Lyria's Generated Song"


@router.message(F.text == UIConfig.GENERATE_BUTTON)
async def cmd_generate(message: Message, state: FSMContext):
    """Старт генерации: показывает подсказку и запрашивает описание песни.

    Доступна только авторизованным и не во время идущей генерации
    или обогащения. Шлёт два сообщения подряд: сначала расширенную
    подсказку, затем копируемый шаблон в <code> — так его удобно
    вставить и заполнить. После этого переводит FSM в ожидание
    описания песни (waiting_for_idea) — дальше диалог ведёт сценарий
    обогащения (enricher_handlers): описание прогоняется через
    обогатитель, пользователь подтверждает или правит результат,
    и только после аппрува вводится название.

    Args:
        message: Входящее сообщение (кнопка «🎵 Сгенерировать»).
        state: FSM-контекст текущего пользователя.
    """
    if not await _require_auth(message):
        return

    data = await state.get_data()
    # Если стейт "в процессе генерации", то не пускаем пользователя к следующей
    if data.get("generating"):
        await message.answer(text="⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
        return
    # Обогащение еще идет - не начинаем новый сценарий
    if data.get("enriching"):
        await message.answer(text="⏳ Дождитесь окончания обогащения или нажмите «❌ Отмена».")
        return

    await message.answer(text=PROMPT_HINT, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await message.answer(text=f"<code>{PROMPT_TEMPLATE}</code>", parse_mode="HTML")

    await state.set_state(PromptEnricherStates.waiting_for_idea)


@router.callback_query(F.data == "retry_generation")
async def retry_generation(callback: CallbackQuery, state: FSMContext):
    """Перезапуск генерации с сохранёнными prompt и title после сбоя.

    Доступен только авторизованным и не во время идущей генерации:
    кнопка повтора могла остаться в чате после logout или запуска
    новой генерации. Если сохранённый промпт потерялся (например,
    после перезапуска бота), предлагает начать заново. Перед запуском
    удаляет старое сообщение с кнопкой, чтобы не плодить «😔» в истории.

    Args:
        callback: Нажатие на кнопку «🔄 Попробовать снова».
        state: FSM-контекст текущего пользователя.
    """
    # Кнопка могла остаться в чате после logout — без этой проверки
    # отозванный ключ позволил бы продолжать генерацию.
    if not await is_authorized(callback.from_user.id):
        await callback.answer(text="Доступ закрыт. Авторизуйтесь заново: /start", show_alert=True)
        await state.clear()
        return

    # Проверка состояния генерации
    data = await state.get_data()
    if data.get("generating"):
        await callback.answer(text="Генерация уже идёт.", show_alert=True)
        return

    prompt = data.get("prompt")
    title = data.get("title", _DEFAULT_TITLE)

    if not prompt or not prompt.strip():
        # Состояние потерялось (перезапуск бота?) — честно просим начать заново, показывая модальное окно
        await callback.answer(text="Начните заново: 🎵 Сгенерировать", show_alert=True)
        await state.clear()
        return

    # Старое сообщение с кнопкой удаляем, чтобы не плодить «😔» в истории
    with contextlib.suppress(Exception):
        await callback.message.delete()
    await callback.answer()

    # Повторно отправляем на генерацию
    await generate_and_send(
        message=callback.message,
        state=state,
        prompt=prompt,
        title=title,
        user_id=callback.from_user.id,
    )
