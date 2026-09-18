"""Проверка оставшихся кредитов OpenRouter."""

import logging

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config

from .auth import _require_auth
from .keyboards import get_main_keyboard
from .utils import notify_owner

log = logging.getLogger(__name__)

router = Router()


@router.message(Command("credits"))
@router.message(F.text == "💳 Кредиты")
async def cmd_credits(message: Message, state: FSMContext):
    """Показывает остаток генераций по данным API OpenRouter.

    Args:
        message: Входящее сообщение (команда или нажатие кнопки).
        state: FSM-контекст; сбрасываем, чтобы прервать незавершённую генерацию.
    """
    if not await _require_auth(message):
        return
    await state.clear()
    if not config.OPENROUTER_API_KEY:
        await message.answer("⚠️ Бот не настроен (API), обратитесь к автору.")
        return

    headers = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url="https://openrouter.ai/api/v1/key", headers=headers)
            if resp.status_code != 200:
                await message.answer(f"❌ Ошибка запроса: {resp.status_code}")
                return

            credits_ = resp.json().get("data", {})
            total = credits_.get("limit")
            remaining = credits_.get("limit_remaining")
            used = credits_.get("usage")

            def _songs_counter(value, msg):
                """Конвертирует сумму в долларах в примерное количество песен.

                Args:
                    value: Сумма (int/float) или None, если API не вернул значение.
                    msg: Заглушка для случая, когда посчитать нельзя.

                Returns:
                    Целое число песен либо msg, если value не число или цена не задана.
                """
                if isinstance(value, (int, float)) and config.SONG_PRICE > 0:
                    return int(value / config.SONG_PRICE)
                return msg

            total_songs = _songs_counter(total, "Без лимита")
            used_songs = _songs_counter(used, "0")
            remaining_songs = _songs_counter(remaining, "Пока не кончится бабосик или Влад не вспомнит про лимит 😁")

            await message.answer(
                f"💳 Баланс песен:\n"
                f"Всего доступно генераций: {total_songs}\n"
                f"Сгенерировано композиций: {used_songs}\n"
                f"Доступное количество генераций: {remaining_songs}",
                reply_markup=get_main_keyboard(),
            )
    except Exception as e:
        await notify_owner(bot=message.bot, context="Проверка кредитов упала (user={message.from_user.id})", err=e)
        await message.answer("❌ Не получилось проверить остатки. Влад уже в курсе 🙂")
