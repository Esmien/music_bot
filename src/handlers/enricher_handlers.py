"""Хендлеры сценария обогащения промпта: идея → обогащение → аппрув → название.

Диалог генерации начинается здесь: описание песни прогоняется через
обогатитель (services.enricher), пользователь подтверждает результат
или шлёт правки (уходят в обогатитель повторно вместе с историей
диалога), и только после аппрува вводится название — дальше сценарий
подхватывают handle_title и конвейер генерации.
"""

import contextlib
import html
import logging
import re
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError

from core.config import UIConfig
from core.utils.error_notify import notify_owner
from fsm.enricher_fsm import PromptEnricherStates
from fsm.generation_fsm import MAX_PROMPT_LEN, GenerationStates
from handlers.auth import is_authorized
from keyboards.default_keyboards import get_cancel_keyboard, get_main_keyboard
from keyboards.enricher_keyboards import (
    CB_PROMPT_APPROVE,
    CB_PROMPT_CANCEL,
    CB_PROMPT_EDIT,
    CB_PROMPT_FALLBACK,
    CB_PROMPT_RETRY,
    get_enrich_failed_keyboard,
    get_prompt_approval_keyboard,
    get_title_keyboard,
)
from services.enricher import enrich_prompt, format_enriched_prompt, save_enriched_prompt

log = logging.getLogger(__name__)

router = Router()

# Подсказка о том, что можно указать в описании песни: длинный копирайт
# вынесен в константу уровня модуля — её отправляет cmd_generate
PROMPT_HINT = (
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
PROMPT_TEMPLATE = "Жанр: \n\nНастроение: \n\nИнструменты: \n\nТемп и ритм: \n\nГолос: \n\nТекст песни: \n"
# Маркеры полей шаблона: детекция формата ввода и отсечение пустого шаблона
_PROMPT_MARKERS = tuple(
    line.strip().split(":")[0] + ":" for line in PROMPT_TEMPLATE.split("\n") if line.strip().endswith(":")
)  # ("Жанр:", "Настроение:", "Инструменты:", "Темп и ритм:", "Голос:", "Текст песни:")
_FIELD_NAMES = tuple(marker[:-1] for marker in _PROMPT_MARKERS)  # тут они уже без ":"

# Парсим шаблон на предмет заполненности полей
_EMPTY_FIELD_RE = re.compile(
    r"^\s*(?:" + "|".join(map(re.escape, _FIELD_NAMES)) + r")\s*:\s*$",
    flags=re.MULTILINE,
)


def _build_generation_prompt(text: str) -> str:
    """Оборачивает текст описания в промпт для сервиса генерации.

    Модель явно инструктируется петь по-русски. Формулировка выбирается
    по маркерам шаблона: структурированный бриф и просто стихи
    оборачиваются по-разному.

    Args:
        text: Обогащённый или исходный текст описания песни.

    Returns:
        Промпт, готовый к отправке в сервис генерации.
    """
    if any(marker in text for marker in _PROMPT_MARKERS):
        # Текст структурирован по шаблону — оставляем структуру
        return (
            "Create a song based on the following brief. "
            "If the lyrics are provided in Russian, sing in Russian.\n\n"
            f"{text}"
        )
    # Пришли просто стихи — не пугаем модель словом "brief"
    return f"Create a song based on these lyrics. If the lyrics are in Russian, sing in Russian.\n\n{text}"


async def _is_actual_enrich(state: FSMContext, enrich_id: str) -> bool:
    """Проверяет, актуален ли запуск обогащения в стейте.

    Args:
        state: Состояние FSM пользователя.
        enrich_id: Маркер запуска обогащения из контекста.

    Returns:
        True, если маркер в FSM совпадает с текущим запуском.
    """
    return (await state.get_data()).get("enrich_id") == enrich_id


async def _ensure_callback_authorized(callback: CallbackQuery, state: FSMContext) -> bool:
    """Гарантирует авторизацию для нажатий по инлайн-кнопкам сценария.

    Кнопки могли остаться в чате после logout: без проверки отозванный
    ключ позволил бы продолжать сценарий обогащения.

    Args:
        callback: Нажатие по инлайн-кнопке сценария.
        state: FSM-контекст текущего пользователя.

    Returns:
        True, если пользователь авторизован; иначе False.
    """
    if await is_authorized(callback.from_user.id):
        return True
    await callback.answer(text="Доступ закрыт. Авторизуйтесь заново: /start", show_alert=True)
    await state.clear()
    return False


async def _save_feedback_best_effort(status: Message, uid: int, initial_prompt: str, enriched_prompt: str) -> None:
    """Сохраняет пару «исходный → обогащённый» промпт в БД, не блокируя сценарий.

    Запись нужна хендлерам оценки после генерации; сбой записи не должен
    останавливать диалог, поэтому ошибка уходит владельцу, а пользователь
    продолжает сценарий.

    Args:
        status: Сообщение-лоадер (нужно ради бота для уведомления владельцу).
        uid: Telegram user_id пользователя.
        initial_prompt: Исходный промпт от пользователя.
        enriched_prompt: Обогащённый промпт.
    """
    try:
        await save_enriched_prompt(tg_id=uid, initial_prompt=initial_prompt, enriched_prompt=enriched_prompt)
    except (SQLAlchemyError, ValueError) as error:
        log.error("Failed to save enriched prompt (user=%s): %s", uid, error)
        with contextlib.suppress(Exception):
            await notify_owner(
                bot=status.bot,
                context=f"Не сохранился обогащённый промпт (user={uid})",
                err=error,
            )


async def _enrich_and_present(status: Message, state: FSMContext, enrich_id: str, uid: int) -> None:
    """Дергает обогатитель и показывает результат или клавиатуру сбоя.

    Первое обогащение уходит с исходной идеей, повтор после правок —
    с историей диалога (идея → прошлый ответ → правки). Перед показом
    результата проверяется актуальность запуска: если сценарий отменили
    («❌ Отмена», /start), результат тихо отбрасывается, чтобы не
    воскрешать сценарий поверх нового состояния.

    В FSM под ключом enriched_prompt хранится сырой JSON-ответ обогатителя
    (он нужен для истории правок, фидбека и промпта генерации), а
    пользователю показывается отформатированная версия через
    format_enriched_prompt.

    Args:
        status: Сообщение-лоадер, которое редактируется по завершении.
        state: FSM-контекст текущего пользователя.
        enrich_id: Маркер запуска обогащения.
        uid: Telegram user_id пользователя.
    """
    data = await state.get_data()
    prompt = data.get("prompt", "")
    enriched_prev = data.get("enriched_prompt")
    edits_text = data.get("pending_edits")

    try:
        if edits_text and enriched_prev:
            # Правки: модель видит исходную идею, свой прошлый ответ и правки
            history = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": enriched_prev},
            ]
            result = await enrich_prompt(prompt=edits_text, history=history)
        else:
            result = await enrich_prompt(prompt=prompt)
    except ValueError as error:
        # Обогатитель не сконфигурирован: ретрай бессмысленен,
        # но пользователь может продолжить без обогащения
        if await _is_actual_enrich(state=state, enrich_id=enrich_id):
            await state.update_data(enriching=False)
        with contextlib.suppress(Exception):
            await notify_owner(
                bot=status.bot,
                context=f"Обогатитель не сконфигурирован (user={uid})",
                err=error,
            )
        with contextlib.suppress(Exception):
            await status.edit_text(
                text="⚠️ Сервис обогащения не настроен. Владелец уже уведомлен.",
                reply_markup=get_enrich_failed_keyboard(),
            )
        return

    # Сценарий отменили, пока обогащение работало, — результат не показываем
    if not await _is_actual_enrich(state=state, enrich_id=enrich_id):
        log.info("Stale enrichment result dropped (user=%s)", uid)
        return

    await state.update_data(enriching=False)

    if result is None:
        # Сетевой сбой или пустой ответ: повтор или продолжение без обогащения
        with contextlib.suppress(Exception):
            await status.edit_text(
                text="😔 Не получилось обогатить описание. Попробуйте ещё раз или продолжите без обогащения.",
                reply_markup=get_enrich_failed_keyboard(),
            )
        return

    await state.update_data(enriched_prompt=result)
    log.info("Enrichment succeeded (user=%s, edits=%s)", uid, bool(edits_text))

    # Пользователю показываем отформатированный текст, в FSM остаётся сырой JSON
    display_text = format_enriched_prompt(raw=result)

    # У бота по умолчанию parse_mode=HTML: ответ модели экранируем,
    # чтобы символы разметки в нём не ломали сообщение
    with contextlib.suppress(Exception):
        await status.edit_text(
            text=(
                "🪄 <b>Я подготовил описание песни:</b>\n\n"
                f"<blockquote expandable>{html.escape(display_text)}</blockquote>\n\n"
                "Подтвердите или пришлите правки."
            ),
            reply_markup=get_prompt_approval_keyboard(),
        )
    await state.set_state(PromptEnricherStates.waiting_for_approval)


@router.message(PromptEnricherStates.waiting_for_idea, F.text)
async def handle_idea(message: Message, state: FSMContext):
    """Принимает описание песни и запускает обогащение.

    Валидирует ввод: непустой текст, лимит длины, нетронутый шаблон.
    Идея сохраняется в FSM под ключом prompt (после аппрува туда же
    запишется промпт, готовый к генерации). Обогащение идет синхронно
    в хендлере: на время запроса выставляется флаг enriching с маркером
    enrich_id — повторные сообщения отсекаются, а отмена сценария
    делает результат «протухшим». Если бот перезапустился посреди
    обогащения, флаг снимается кнопкой «❌ Отмена» или /start.

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
        await message.answer(text="Шаблон пришёл пустым 🙂 Заполните хотя бы поле «Текст песни».")
        return

    data = await state.get_data()
    if data.get("enriching"):
        await message.answer(text="⏳ Дождитесь окончания обогащения или нажмите «❌ Отмена».")
        return

    enrich_id = uuid4().hex
    # Начинаем новый сценарий: гасим следы предыдущего запуска, иначе
    # старые enriched_prompt/pending_edits уведут обогащение по пути правок
    await state.update_data(
        prompt=prompt,
        enriched_prompt=None,
        pending_edits=None,
        enriching=True,
        enrich_id=enrich_id,
    )
    status = await message.answer(text="🔄 Обогащаю описание песни… Это может занять до пары минут.")

    await _enrich_and_present(status=status, state=state, enrich_id=enrich_id, uid=message.from_user.id)


@router.callback_query(PromptEnricherStates.waiting_for_approval, F.data == CB_PROMPT_APPROVE)
async def handle_prompt_approve(callback: CallbackQuery, state: FSMContext):
    """Аппрув обогащённого промпта: финализация и запрос названия песни.

    Обогащённый текст оборачивается в промпт для генерации и пишется
    в FSM под ключом prompt — его же используют конвейер генерации
    и повтор после сбоя. Инлайн-клавиатура снимается, чтобы сценарий
    нельзя было пройти повторно по оставшейся в чате кнопке.

    Args:
        callback: Нажатие на кнопку «✅ Подтвердить».
        state: FSM-контекст текущего пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    data = await state.get_data()
    enriched = data.get("enriched_prompt")
    if not enriched:
        # Сессия потерялась (перезапуск бота / чистка FSM) — просим начать заново
        await callback.answer(text="Начните заново: 🎵 Сгенерировать", show_alert=True)
        await state.clear()
        return

    # Фидбек пишем до финализации: в data["prompt"] ещё исходная идея пользователя
    await _save_feedback_best_effort(
        status=callback.message,
        uid=callback.from_user.id,
        initial_prompt=data.get("prompt"),
        enriched_prompt=enriched,
    )

    await state.update_data(prompt=_build_generation_prompt(text=enriched))
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(GenerationStates.waiting_for_title)
    await callback.message.answer(
        text=(f"🎤 Введите название песни или нажмите «Оставить как есть»:\n({UIConfig.DEFAULT_TITLE})"),
        reply_markup=get_title_keyboard(),
    )


@router.callback_query(PromptEnricherStates.waiting_for_approval, F.data == CB_PROMPT_EDIT)
async def handle_prompt_edit(callback: CallbackQuery, state: FSMContext):
    """Переводит сценарий в ожидание правок обогащённого промпта.

    Args:
        callback: Нажатие на кнопку «✏️ Изменить».
        state: FSM-контекст текущего пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    await state.set_state(PromptEnricherStates.waiting_for_edits)
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        text="✏️ Пришлите правки: что изменить в описании песни.",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(PromptEnricherStates.waiting_for_edits, F.text)
async def handle_prompt_edits(message: Message, state: FSMContext):
    """Принимает правки и повторно обогащает промпт с историей диалога.

    Правки уходят в обогатитель вместе с исходной идеей и прошлым
    вариантом обогащения. Результат снова показывается на аппрув;
    при сбое — кнопки повтора и продолжения без обогащения.

    Args:
        message: Входящее сообщение с правками.
        state: FSM-контекст текущего пользователя.
    """
    edits_text = message.text.strip()

    if not edits_text:
        await message.answer(text="Пожалуйста, введите непустые правки.")
        return
    if len(edits_text) > MAX_PROMPT_LEN:
        await message.answer(text=f"Слишком длинный текст: {len(edits_text)} символов. Максимум — {MAX_PROMPT_LEN}.")
        return

    data = await state.get_data()
    if not data.get("prompt"):
        # Сессия потерялась (перезапуск бота / чистка FSM) — начинаем заново
        await message.answer(
            text="😔 Сессия обогащения потерялась. Начните заново — нажмите «🎵 Сгенерировать».",
            reply_markup=get_main_keyboard(),
        )
        await state.clear()
        return
    if data.get("enriching"):
        await message.answer(text="⏳ Дождитесь окончания обогащения или нажмите «❌ Отмена».")
        return

    enrich_id = uuid4().hex
    await state.update_data(pending_edits=edits_text, enriching=True, enrich_id=enrich_id)
    status = await message.answer(text="🔄 Обогащаю с учётом правок… Это может занять до пары минут.")

    await _enrich_and_present(status=status, state=state, enrich_id=enrich_id, uid=message.from_user.id)


@router.callback_query(
    StateFilter(PromptEnricherStates.waiting_for_idea, PromptEnricherStates.waiting_for_edits),
    F.data == CB_PROMPT_RETRY,
)
async def handle_prompt_retry(callback: CallbackQuery, state: FSMContext):
    """Повторный запуск обогащения после сбоя.

    Доступен только авторизованным и пока сценарий не ушёл дальше
    (стейт waiting_for_idea или waiting_for_edits): кнопка могла
    остаться в чате после аппрува или отмены. Контекст берётся из FSM
    в момент нажатия: первый запуск обогащает исходную идею, повтор
    после правок — историю диалога с правками. Сообщение с кнопкой
    сбоя переиспользуется как лоадер.

    Args:
        callback: Нажатие на кнопку «🔄 Попробовать снова».
        state: FSM-контекст текущего пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    data = await state.get_data()
    if data.get("enriching"):
        await callback.answer(text="Обогащение уже выполняется.", show_alert=True)
        return
    if not data.get("prompt"):
        await callback.answer(text="Начните заново: 🎵 Сгенерировать", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    enrich_id = uuid4().hex
    await state.update_data(enriching=True, enrich_id=enrich_id)
    with contextlib.suppress(Exception):
        await callback.message.edit_text(text="🔄 Обогащаю описание песни… Это может занять до пары минут.")

    await _enrich_and_present(status=callback.message, state=state, enrich_id=enrich_id, uid=callback.from_user.id)


@router.callback_query(
    StateFilter(PromptEnricherStates.waiting_for_idea, PromptEnricherStates.waiting_for_edits),
    F.data == CB_PROMPT_FALLBACK,
)
async def handle_prompt_fallback(callback: CallbackQuery, state: FSMContext):
    """Продолжает сценарий без обогащения после сбоя.

    Берёт последний удачный обогащённый вариант, если он был (правки
    могли не обогатиться, но предыдущая версия лучше сырого текста),
    иначе — исходную идею. Текст оборачивается в промпт для генерации,
    состояние переводится в ожидание названия.

    Args:
        callback: Нажатие на кнопку «⏭ Без обогащения».
        state: FSM-контекст текущего пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    data = await state.get_data()
    prompt = data.get("prompt")
    if not prompt:
        await callback.answer(text="Начните заново: 🎵 Сгенерировать", show_alert=True)
        await state.clear()
        return

    await callback.answer()
    # Последний удачный вариант обогащения приоритетнее сырого текста
    final_text = data.get("enriched_prompt") or prompt
    # Фидбек пишем до финализации: в data["prompt"] ещё исходная идея пользователя
    await _save_feedback_best_effort(
        status=callback.message,
        uid=callback.from_user.id,
        initial_prompt=prompt,
        enriched_prompt=final_text,
    )
    await state.update_data(prompt=_build_generation_prompt(text=final_text))
    log.info("Enrichment fallback used (user=%s)", callback.from_user.id)
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(GenerationStates.waiting_for_title)
    await callback.message.answer(
        text=(f"🎤 Введите название песни или нажмите «Оставить как есть»:\n({UIConfig.DEFAULT_TITLE})"),
        reply_markup=get_title_keyboard(),
    )


@router.callback_query(
    StateFilter(PromptEnricherStates.waiting_for_approval, GenerationStates.waiting_for_title),
    F.data == CB_PROMPT_CANCEL,
)
async def handle_prompt_cancel(callback: CallbackQuery, state: FSMContext):
    """Отменяет сценарий обогащения и возвращает в главное меню.

    Args:
        callback: Нажатие на кнопку «❌ Отменить».
        state: FSM-контекст текущего пользователя.
    """
    if not await _ensure_callback_authorized(callback=callback, state=state):
        return

    await state.clear()
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_text(text="Сценарий обогащения отменён.")
    await callback.message.answer(text="Возвращаю в главное меню.", reply_markup=get_main_keyboard())
