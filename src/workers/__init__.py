"""Пакет воркеров для асинхронной обработки команд."""

from domains.enricher.worker import enrich_prompt_task  # type: ignore[attr-defined]

__all__ = ["enrich_prompt_task"]
