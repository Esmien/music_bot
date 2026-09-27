"""Telegram-обработчики домена проверки кредитов OpenRouter."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from domains.auth.handlers import require_auth
from domains.base.keyboards import CREDITS_BUTTON
from domains.credits.worker import handle_get_credits
from shared.contracts.commands import GetCredits

log = logging.getLogger(__name__)

router = Router(name="credits")


@router.message(Command("credits"))
@router.message(F.text == CREDITS_BUTTON)
async def cmd_credits(message: Message, state: FSMContext) -> None:
    """Публикует команду GetCredits для проверки кредитов OpenRouter.

    Args:
        message: Входящее сообщение.
        state: FSM-контекст; очищается для отмены незавершённого сценария.
    """
    if not await require_auth(message):
        return
    await state.clear()

    command = GetCredits(chat_id=message.chat.id)
    await handle_get_credits.kiq(command)
