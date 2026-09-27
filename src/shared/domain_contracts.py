"""Контракты (порты) для междоменного взаимодействия без циклических зависимостей."""

from typing import Protocol

from aiogram.fsm.context import FSMContext
from aiogram.types import Message


class EnrichmentFlowStarter(Protocol):
    """Порт для запуска сценария обогащения из домена generation."""

    async def start_enrichment(self, message: Message, state: FSMContext) -> None:
        """Запускает сценарий обогащения промпта.

        Args:
            message: Telegram-сообщение для ответа.
            state: FSM-контекст для управления состоянием.
        """
        ...


class GenerationFlowStarter(Protocol):
    """Порт для запуска сценария генерации из домена enricher."""

    async def start_title_input(
        self,
        message: Message,
        state: FSMContext,
        prompt: str,
    ) -> None:
        """Запускает фазу ввода названия после обогащения.

        Args:
            message: Telegram-сообщение для ответа.
            state: FSM-контекст для управления состоянием.
            prompt: Обогащённый текст промпта.
        """
        ...
