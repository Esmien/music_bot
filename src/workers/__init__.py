"""Пакет воркеров для асинхронной обработки команд."""

from workers.enricher_worker import enrich_prompt_task

__all__ = ["enrich_prompt_task"]
