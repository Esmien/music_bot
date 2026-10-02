"""Общие обработчики домена base: старт бота и отмена текущего действия."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.redis import request_generation_cancel
from core.utils.fsm_helpers import get_fsm_data
from domains.auth.registries.auth_registry import add_pending_auth, discard_pending_auth
from domains.auth.service import is_authorized
from domains.base import base_messasges
from domains.base.keyboards import CANCEL_BUTTON, get_main_keyboard
from domains.base.service import get_last_generated_title
from domains.generation.registries.task_registry import get_active_task, unregister_active_task
from domains.generation.state_models import GenerationFlowState

router = Router(name="base")


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    """/start: приветствие и проверка статуса авторизации.

    Args:
        message: Входящее сообщение с командой /start.
        state: FSM-контекст, очищаемый для сброса незавершённых сценариев.
    """
    await state.clear()
    uid = message.from_user.id

    if await is_authorized(uid=uid):
        await discard_pending_auth(uid=uid)
        last_title = await get_last_generated_title(uid=uid)
        if last_title:
            tg_name = message.from_user.first_name or message.from_user.username or base_messasges.START_NAME_FALLBACK
            await message.answer(
                text=base_messasges.START_RETURNING.format(tg_name=tg_name, last_title=last_title),
                reply_markup=get_main_keyboard(),
            )
        else:
            await message.answer(
                text=base_messasges.START_FIRST_VISIT,
                reply_markup=get_main_keyboard(),
            )
        return

    await add_pending_auth(uid=uid)
    await message.answer(
        text=base_messasges.START_AUTH_REQUIRED,
        reply_markup=None,
    )


@router.message(Command("cancel"))
@router.message(F.text == CANCEL_BUTTON)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отменяет текущий сценарий и возвращает пользователя в главное меню.

    Args:
        message: Сообщение с командой /cancel или кнопкой отмены.
        state: FSM-контекст текущего пользователя.
    """
    flow_state = await get_fsm_data(state=state, model_class=GenerationFlowState)
    if flow_state.gen_id is not None and flow_state.generating:
        await request_generation_cancel(gen_id=flow_state.gen_id)

    task = get_active_task(uid=message.from_user.id)
    if task is not None and not task.done():
        task.cancel()
        await unregister_active_task(uid=message.from_user.id, task=task)
    await state.clear()
    await message.answer(text=base_messasges.CANCEL_ACTION, reply_markup=get_main_keyboard())
