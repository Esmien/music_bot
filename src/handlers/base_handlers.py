"""Универсальные хендлеры, не привязанные к шагам конкретных сценариев.

Здесь живут обработчики «глобального» действия — отмена текущего
сценария из любого FSM-состояния.
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.config import UIConfig
from fsm.evaluation_fsm import active_tasks
from keyboards.default_keyboards import get_main_keyboard

router = Router(name="base")


@router.message(Command("cancel"))
@router.message(F.text == UIConfig.CANCEL_BUTTON)
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отмена текущего сценария: сброс FSM и возврат на главный экран.

    Гасит живую задачу генерации из active_tasks (та удалит сообщение
    прогресса и завершится как отменённая), иначе она продолжит крутиться
    и позже «внезапно» пришлёт песню или «😔 Не получилось» поверх отмены.

    Args:
        message: Сообщение с командой /cancel или кнопкой «❌ Отмена».
        state: FSM-контекст текущего пользователя.
    """
    task = active_tasks.get(message.from_user.id)
    if task is not None and not task.done():
        task.cancel()
    await state.clear()
    await message.answer(text="❌ Действие отменено.", reply_markup=get_main_keyboard())
