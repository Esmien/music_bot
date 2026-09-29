"""Pydantic-модели FSM-данных для домена обогащения промптов."""

from pydantic import BaseModel


class EnrichmentFlowState(BaseModel):
    """FSM-данные сценария обогащения промпта.

    Атрибуты:
        prompt: Исходный текст описания песни от пользователя.
        enriched_prompt: Обогащённый промпт после вызова API обогатителя.
        pending_edits: Текст правок от пользователя для повторного обогащения.
        enriching: Флаг активного обогащения (защита от параллельных запусков).
        enrich_id: Маркер запуска обогащения (защита от race condition).
    """

    prompt: str | None = None
    enriched_prompt: str | None = None
    pending_edits: str | None = None
    enriching: bool = False
    enrich_id: str | None = None
    retry_count: int = 0
