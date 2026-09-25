"""Telegram-обработчики домена проверки кредитов OpenRouter."""

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
from domains.credits.service import get_credits_summary

log = logging.getLogger(__name__)

router = Router(name="credits")


@router.message(Command("credits"))
@router.message(F.text == UIConfig.CREDITS_BUTTON)
async def cmd_credits(message: Message, state: FSMContext) -> None:
    """Показывает остаток генераций по данным API OpenRouter.

    Args:
        message: Входящее сообщение.
        state: FSM-контекст; очищается для отмены незавершённого сценария.
    """
    if not await _require_auth(message):
        return
    await state.clear()

    api_key = settings.bot.OPENROUTER_API_KEY
    if not api_key:
        await notify_owner(
            bot=message.bot,
            context="Не настроен ключ API",
            err=APINotSet("Provider key not set."),
        )
        await message.answer(text="⚠️ Бот не настроен, владелец уже уведомлен.")
        return

    try:
        summary = await get_credits_summary(
            api_key=api_key,
            song_price=settings.generation.SONG_PRICE,
        )
        if summary.status_code != 200:
            await message.answer(text=f"❌ Ошибка запроса: {summary.status_code}")
            return

        await message.answer(
            text=(
                f"💳 Баланс песен:\n"
                f"Всего доступно генераций: {summary.total_songs}\n"
                f"Сгенерировано композиций: {summary.used_songs}\n"
                f"Доступное количество генераций: {summary.remaining_songs}"
            ),
            reply_markup=get_main_keyboard(),
        )
    except httpx.HTTPError as error:
        log.error("Failed to check OpenRouter credits (user=%s)", message.from_user.id)
        await notify_owner(
            bot=message.bot,
            context=f"Проверка кредитов упала (user={message.from_user.id})",
            err=error,
        )
        await message.answer(text="❌ Не получилось проверить остатки. Владелец уведомлен.")
