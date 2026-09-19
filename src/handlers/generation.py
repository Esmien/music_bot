"""Хендлеры диалога генерации: команда, промпт, название, повтор и отмена.

Механика запуска генерации (локи, прогресс, отмена, обработка сбоев)
вынесена в generation_pipeline, FSM-состояния и лимиты — в generation_fsm.
"""

import contextlib
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers.auth import _require_auth, is_authorized
from handlers.generation_fsm import MAX_PROMPT_LEN, MAX_TITLE_LEN, GenerationStates
from handlers.generation_pipeline import generate_and_send
from handlers.state import active_tasks
from keyboards.default_keyboards import get_cancel_keyboard, get_main_keyboard

router = Router()

# Подсказка о том, что можно указать в описании песни: длинный копирайт
# вынесен в константу, чтобы не раздувать хендлер
_PROMPT_HINT = (
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

# Копируемый шаблон описания песни
_PROMPT_TEMPLATE = "Жанр: \n\nНастроение: \n\nИнструменты: \n\nТемп и ритм: \n\nГолос: \n\nТекст песни: \n"
# Маркеры полей шаблона: детекция формата ввода и отсечение пустого шаблона
_PROMPT_MARKERS = tuple(
    line.strip().split(":")[0] + ":" for line in _PROMPT_TEMPLATE.split("\n") if line.strip().endswith(":")
)  # ("Жанр:", "Настроение:", "Инструменты:", "Темп и ритм:", "Голос:", "Текст песни:")
_FIELD_NAMES = tuple(marker[:-1] for marker in _PROMPT_MARKERS)  # тут они уже без ":"

# Парсим шаблон на предмет заполненности полей
_EMPTY_FIELD_RE = re.compile(
    r"^\s*(?:" + "|".join(map(re.escape, _FIELD_NAMES)) + r")\s*:\s*$",
    flags=re.MULTILINE,
)
_DEFAULT_TITLE = "Lyria's Generated Song"


@router.message(F.text == "🎵 Сгенерировать")
async def cmd_generate(message: Message, state: FSMContext):
    """Старт генерации: показывает подсказку и запрашивает описание песни.

    Доступна только авторизованным и не во время идущей генерации.
    Шлёт два сообщения подряд: сначала расширенную подсказку, затем
    копируемый шаблон в <code> — так его удобно вставить и заполнить.
    После этого переводит FSM в ожидание описания песни.

    Args:
        message: Входящее сообщение (кнопка «🎵 Сгенерировать»).
        state: FSM-контекст текущего пользователя.
    """
    if not await _require_auth(message):
        return

    # Если стейт "в процессе генерации", то не пускаем пользователя к следующей
    if (await state.get_data()).get("generating"):
        await message.answer(text="⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
        return

    await message.answer(text=_PROMPT_HINT, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    await message.answer(text=f"<code>{_PROMPT_TEMPLATE}</code>", parse_mode="HTML")

    await state.set_state(GenerationStates.waiting_for_prompt)


@router.message(GenerationStates.waiting_for_prompt, F.text == "❌ Отмена")
@router.message(GenerationStates.waiting_for_title, F.text == "❌ Отмена")
async def cmd_cancel_generation(message: Message, state: FSMContext):
    """Отмена генерации по кнопке «❌ Отмена».

    Гасит живую задачу генерации из active_tasks (та удалит сообщение
    прогресса и завершится как отменённая), сбрасывает FSM и возвращает
    основную клавиатуру.

    Args:
        message: Входящее сообщение с кнопкой «❌ Отмена».
        state: FSM-контекст текущего пользователя.
    """
    # Гасим живую задачу генерации, если она есть: иначе она продолжит крутиться
    # и позже «внезапно» пришлёт песню или «😔 Не получилось» поверх отмены.
    task = active_tasks.get(message.from_user.id)
    if task is not None and not task.done():
        task.cancel()
    await state.clear()
    await message.answer(text="❌ Генерация отменена.", reply_markup=get_main_keyboard())


@router.message(GenerationStates.waiting_for_prompt, F.text)
async def handle_prompt(message: Message, state: FSMContext):
    """Принимает и валидирует описание песни, запрашивает название.

    Если пользователь заполнил шаблон (есть маркеры вида "Жанр:"),
    промпт оборачивается в бриф с явным указанием петь по-русски.
    Если пришли просто стихи — используется более мягкая формулировка.
    Подготовленный промпт сохраняется в FSM, состояние переключается
    на ожидание названия.
    Если шаблон заполнен, но поле «Текст песни» пустое, песня уходит
    в генерацию без лирики — модель сочинит текст сама
    (TODO: уточняющий вопрос перед генерацией).
    Подготовленный промпт сохраняется в FSM, состояние переключается
    на ожидание названия.

    Args:
        message: Входящее сообщение с описанием песни.
        state: FSM-контекст текущего пользователя.
    """
    prompt = message.text.strip()

    if not prompt:
        await message.answer(text="Пожалуйста, введите непустой текст.")
        return
    if len(prompt) > MAX_PROMPT_LEN:
        await message.answer(text=f"Слишком длинный текст: {len(prompt)} символов. Максимум — {MAX_PROMPT_LEN}.")
        return

    # Отсекаем нетронутый шаблон: все поля пустые
    filled = _EMPTY_FIELD_RE.sub("", prompt)
    if not filled.strip():
        await message.answer("Шаблон пришёл пустым 🙂 Заполни хотя бы поле «Текст песни».")
        return

    if any(marker in prompt for marker in _PROMPT_MARKERS):
        # TODO: если поле «Текст песни» пустое —
        #  уточнить у пользователя перед генерацией
        #  (модель сочинит текст сама, ~$1 за прогон)

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

    await state.update_data(prompt=structured_prompt)
    await state.set_state(GenerationStates.waiting_for_title)
    await message.answer(text="🎤 Введите название песни:", reply_markup=get_cancel_keyboard())


@router.message(GenerationStates.waiting_for_title, F.text)
async def handle_title(message: Message, state: FSMContext):
    """Принимает название, генерирует песню и отправляет аудиофайл.

    Валидирует название, сохраняет его в FSM (пригодится для повтора
    после сбоя) и запускает фоновую задачу generate_and_send.

    Args:
        message: Входящее сообщение с названием песни.
        state: FSM-контекст текущего пользователя.
    """
    title = message.text.strip()
    if not title:
        await message.answer(text="Пожалуйста, введите непустое название.")
        return
    if len(title) > MAX_TITLE_LEN:
        await message.answer(text=f"Слишком длинное название. Максимум {MAX_TITLE_LEN} символов.")
        return

    data = await state.get_data()
    if data.get("generating"):
        await message.answer(text="⏳ Дождитесь окончания текущей генерации или нажмите «❌ Отмена».")
        return

    prompt = data.get("prompt")
    if not prompt or not prompt.strip():
        # Промпт потерялся (перезапуск бота / гонка кнопок / чистка FSM) —
        # на генерацию с пустой строкой не отправляем
        await message.answer(
            text="😔 Описание песни потерялось. Начните заново — отправьте стихи или шаблон.",
            reply_markup=get_main_keyboard(),
        )
        await state.clear()
        return

    # title кладём в FSM — пригодится для retry
    await state.update_data(title=title)

    # Все данные собраны, отправляем на генерацию
    await generate_and_send(message=message, state=state, prompt=prompt, title=title, user_id=message.from_user.id)


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
