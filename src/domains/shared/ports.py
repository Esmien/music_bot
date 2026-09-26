"""Конкретные реализации междоменных портов."""

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from domains.base.keyboards import get_cancel_keyboard
from domains.enricher.enricher_messages import PROMPT_HINT, PROMPT_TEMPLATE
from domains.enricher.fsm import PromptEnricherStates
from domains.enricher.keyboards import get_title_keyboard
from domains.generation.fsm import GenerationStates
from domains.generation.generation_messages import TITLE_PROMPT_TEXT


class EnrichmentFlowStarterImpl:
    """Конкретная реализация для запуска сценария обогащения."""

    async def start_enrichment(self, message: Message, state: FSMContext) -> None:
        """Запускает сценарий обогащения промпта.

        Args:
            message: Telegram-сообщение для ответа.
            state: FSM-контекст для управления состоянием.
        """
        await message.answer(text=PROMPT_HINT, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
        await message.answer(text=f"<code>{PROMPT_TEMPLATE}</code>", parse_mode="HTML")
        await state.set_state(PromptEnricherStates.waiting_for_idea)


class GenerationFlowStarterImpl:
    """Конкретная реализация для запуска сценария генерации."""

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
        await state.update_data(prompt=prompt)
        await state.set_state(GenerationStates.waiting_for_title)
        await message.answer(
            text=TITLE_PROMPT_TEXT,
            reply_markup=get_title_keyboard(),
        )


# Экземпляры-синглтоны для использования в хендлерах
enrichment_flow_starter = EnrichmentFlowStarterImpl()
generation_flow_starter = GenerationFlowStarterImpl()
