"""Pydantic-модели FSM-данных для домена генерации."""

from pydantic import BaseModel


class GenerationFlowState(BaseModel):
    """FSM-данные сценария генерации песни.

    Атрибуты:
        prompt: Подготовленный промпт для генерации (из обогатителя или напрямую).
        title: Название трека, введённое пользователем.
        generating: Флаг активной генерации (защита от параллельных запусков).
        gen_id: Маркер запуска генерации (защита от race condition).
    """

    prompt: str | None = None
    title: str | None = None
    generating: bool = False
    gen_id: int | None = None
