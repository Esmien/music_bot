"""Утилиты для работы с типизированными FSM-данными через Pydantic."""

from aiogram.fsm.context import FSMContext
from pydantic import BaseModel


async def get_fsm_data[T: BaseModel](state: FSMContext, model_class: type[T]) -> T:
    """Читает FSM-данные и парсит их в Pydantic-модель.

    Args:
        state: FSM-контекст пользователя.
        model_class: Класс Pydantic-модели для десериализации.

    Returns:
        Экземпляр модели с данными из FSM.
    """
    data = await state.get_data()
    return model_class.model_validate(data)


async def update_fsm_data(state: FSMContext, model: BaseModel) -> None:
    """Записывает Pydantic-модель в FSM через model_dump.

    Args:
        state: FSM-контекст пользователя.
        model: Экземпляр Pydantic-модели для сериализации.

    Returns:
        None.
    """
    await state.update_data(**model.model_dump(exclude_none=False))
