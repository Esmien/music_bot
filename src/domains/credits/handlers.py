"""Telegram-обработчики домена проверки кредитов OpenRouter."""

import logging

import httpx
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.config import settings
from core.utils.error_notify import notify_owner
from core.utils.exceptions import APINotSet
from domains.auth.handlers import _require_auth
from domains.base.keyboards import CREDITS_BUTTON, get_main_keyboard
from domains.credits.credits_messages import (
    API_KEY_NOT_CONFIGURED_CONTEXT,
    API_KEY_NOT_SET_ERROR,
    CREDITS_API_ERROR,
    CREDITS_API_KEY_NOT_CONFIGURED,
    CREDITS_CHECK_FAILED,
    CREDITS_CHECK_FAILED_LOG,
    CREDITS_CHECK_FAILED_OWNER_CONTEXT,
    CREDITS_SUMMARY,
)
from domains.credits.service import get_credits_summary

log = logging.getLogger(__name__)

router = Router(name="credits")


@router.message(Command("credits"))
@router.message(F.text == CREDITS_BUTTON)
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
            context=API_KEY_NOT_CONFIGURED_CONTEXT,
            err=APINotSet(API_KEY_NOT_SET_ERROR),
        )
        await message.answer(text=CREDITS_API_KEY_NOT_CONFIGURED)
        return

    try:
        summary = await get_credits_summary(
            api_key=api_key,
            song_price=settings.generation.SONG_PRICE,
        )
        if summary.status_code != 200:
            await message.answer(text=CREDITS_API_ERROR.format(status_code=summary.status_code))
            return

        await message.answer(
            text=CREDITS_SUMMARY.format(
                total_songs=summary.total_songs,
                used_songs=summary.used_songs,
                remaining_songs=summary.remaining_songs,
            ),
            reply_markup=get_main_keyboard(),
        )
    except httpx.HTTPError as error:
        log.error(CREDITS_CHECK_FAILED_LOG, message.from_user.id)
        await notify_owner(
            bot=message.bot,
            context=CREDITS_CHECK_FAILED_OWNER_CONTEXT.format(user_id=message.from_user.id),
            err=error,
        )
        await message.answer(text=CREDITS_CHECK_FAILED)
