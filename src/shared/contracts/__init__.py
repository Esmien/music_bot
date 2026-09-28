"""Контракты для взаимодействия между доменами через очередь задач.

Версия контрактов: 1.0.0

Правило: payload содержит только сериализуемые примитивы (int, str, bool, list, dict).
Никаких объектов aiogram (Message, FSMContext) — они не переживут JSON-сериализацию.
"""

from shared.contracts.commands import CheckCreditsCommand, RunGeneration, StartEnrichment
from shared.contracts.events import EnrichmentCompleted, EvaluationCompleted, GenerationFailed, GenerationSucceeded

__version__ = "1.0.0"

__all__ = [
    # Commands
    "StartEnrichment",
    "RunGeneration",
    "CheckCreditsCommand",
    # Events
    "GenerationSucceeded",
    "GenerationFailed",
    "EnrichmentCompleted",
    "EvaluationCompleted",
    "__version__",
]
