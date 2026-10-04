"""Утилиты для работы с типизированными FSM-данными через Pydantic.

Модуль предоставляет type-safe обёртки над FSMContext для чтения и записи данных."""

from typing import Protocol

from pydantic import BaseModel


class FSMContextLike(Protocol):
    """Протокол для FSM-контекста, совместимого с aiogram.FSMContext."""

    async def get_data(self) -> dict:
        """Получить данные из FSM."""
        ...

    async def update_data(self, **kwargs: object) -> None:
        """Обновить данные в FSM."""
        ...


async def get_fsm_data[T: BaseModel](*, state: FSMContextLike, model_class: type[T]) -> T:
    """Читает FSM-данные и парсит их в Pydantic-модель.

    Args:
        state: FSM-контекст пользователя.
        model_class: Класс Pydantic-модели для десериализации.

    Returns:
        Экземпляр модели с данными из FSM.
    """
    data = await state.get_data()
    return model_class.model_validate(data)


async def update_fsm_data(*, state: FSMContextLike, model: BaseModel) -> None:
    """Записывает Pydantic-модель в FSM через model_dump.

    Args:
        state: FSM-контекст пользователя.
        model: Экземпляр Pydantic-модели для сериализации.
    """
    await state.update_data(**model.model_dump(exclude_none=False))
