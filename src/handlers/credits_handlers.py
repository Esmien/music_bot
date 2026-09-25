"""Проверка оставшихся кредитов OpenRouter."""

import logging

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.config import UIConfig, settings
from core.utils.error_notify import notify_owner
from core.utils.exceptions import APINotSet
from domains.auth.handlers import _require_auth
from domains.base.keyboards import get_main_keyboard

log = logging.getLogger(__name__)

router = Router()


@router.message(Command("credits"))
@router.message(F.text == UIConfig.CREDITS_BUTTON)
async def cmd_credits(message: Message, state: FSMContext):
    """Показывает остаток генераций по данным API OpenRouter.

    Args:
        message: Входящее сообщение.
        state: FSM-контекст; очищается для отмены незавершённого сценария.
    """
    if not await _require_auth(message):
        return
    await state.clear()

    if not settings.bot.OPENROUTER_API_KEY:
        await notify_owner(bot=message.bot, context="Не настроен ключ API", err=APINotSet("Provider key not set."))
        await message.answer(text="⚠️ Бот не настроен, владелец уже уведомлен.")
        return

    headers = {"Authorization": f"Bearer {settings.bot.OPENROUTER_API_KEY}"}
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
                """Конвертирует сумму в долларах в примерное число песен.

                Args:
                    value: Сумма или None, если API не вернул значение.
                    placeholder: Заглушка, если посчитать нельзя.

                Returns:
                    Число песен либо placeholder.
                """
                if isinstance(value, (int, float)) and settings.generation.SONG_PRICE > 0:
                    return int(value / settings.generation.SONG_PRICE)
                return placeholder

            total_songs = _songs_counter(value=total, placeholder="Без лимита")
            used_songs = _songs_counter(value=used, placeholder="0")
            remaining_songs = _songs_counter(value=remaining, placeholder="Невозможно посчитать")

            await message.answer(
                text=(
                    f"💳 Баланс песен:\n"
                    f"Всего доступно генераций: {total_songs}\n"
                    f"Сгенерировано композиций: {used_songs}\n"
                    f"Доступное количество генераций: {remaining_songs}"
                ),
                reply_markup=get_main_keyboard(),
            )
    except httpx.HTTPError as error:
        await notify_owner(
            bot=message.bot,
            context=f"Проверка кредитов упала (user={message.from_user.id})",
            err=error,
        )
        await message.answer(text="❌ Не получилось проверить остатки. Владелец уведомлен.")
