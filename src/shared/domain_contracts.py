"""Telegram flow contracts для междоменного взаимодействия.

Эти контракты являются Telegram-специфичные для application layer.

Границы aiogram-зависимости:
----------------------------
Эти Protocol принимают aiogram-типы (Message, FSMContext) и используются в handlers
для междоменных вызовов между доменами.

Domain service НЕ должен импортировать aiogram и работает только
с примитивами (int, str, bytes) и domain models.

Разрешённые импорты aiogram:
- src/domains/*/handlers.py — Telegram-хендлеры
- src/shared/domain_ports.py — Реализации контрактов
- src/shared/domain_contracts.py — Protocol с aiogram-типами (этот файл)
- src/core/__init__.py — Сборка Router

Запрещённые импорты aiogram:
- src/domains/*/service.py — Domain services
- src/core/**/*.py (кроме __init__.py) — Core utilities
"""

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
