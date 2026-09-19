"""Проверка оставшихся кредитов OpenRouter."""

import logging

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config
from handlers.auth import _require_auth
from keyboards.default_keyboards import get_main_keyboard
from utils.error_notify import notify_owner
from utils.exceptions import APINotSet

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
        await notify_owner(bot=message.bot, context="Не настроен ключ API", err=APINotSet("Provider key not set."))
        await message.answer(text="⚠️ Бот не настроен, владелец уже уведомлен.")
        return

    headers = {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url="https://openrouter.ai/api/v1/key", headers=headers)
            if resp.status_code != 200:
                await message.answer(text=f"❌ Ошибка запроса: {resp.status_code}")
                return

            key_info = resp.json().get("data", {})
            total = key_info.get("limit")
            remaining = key_info.get("limit_remaining")
            used = key_info.get("usage")

            def _songs_counter(value: int | float | None, placeholder: str) -> int | str:
                """Конвертирует сумму в долларах в примерное количество песен.

                Args:
                    value: Сумма (int/float) или None, если API не вернул значение.
                    placeholder: Заглушка для случая, когда посчитать нельзя.

                Returns:
                    Целое число песен либо placeholder, если value не число или цена не задана.
                """
                if isinstance(value, (int, float)) and config.SONG_PRICE > 0:
                    return int(value / config.SONG_PRICE)
                return placeholder

            total_songs = _songs_counter(value=total, placeholder="Без лимита")
            used_songs = _songs_counter(value=used, placeholder="0")
            remaining_songs = _songs_counter(value=remaining, placeholder="Невозможно посчитать")

            await message.answer(
                text=f"💳 Баланс песен:\n"
                f"Всего доступно генераций: {total_songs}\n"
                f"Сгенерировано композиций: {used_songs}\n"
                f"Доступное количество генераций: {remaining_songs}",
                reply_markup=get_main_keyboard(),
            )
    except httpx.HTTPError as error:
        await notify_owner(
            bot=message.bot,
            context=f"Проверка кредитов упала (user={message.from_user.id})",
            err=error,
        )
        await message.answer(text="❌ Не получилось проверить остатки. Владелец уведомлен.")
