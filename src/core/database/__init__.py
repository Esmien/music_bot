"""Пакет работы с БД: доменные модели, подключение и инициализация схемы.

Модуль реэкспортирует:
- Base: базовый класс для ORM-моделей
- engine, SessionLocal: подключение к БД
- init_db: инициализация схемы
- Доменные модели: User, Generation, GenerationFeedback
"""

from core.database.engine import SessionLocal, engine, get_session, init_db
from core.database.models import Base
from domains.base.models import User
from domains.feedback.models import GenerationFeedback
from domains.generation.models import Generation, GenerationStatus

__all__ = [
    "Base",
    "Generation",
    "GenerationFeedback",
    "GenerationStatus",
    "SessionLocal",
    "User",
    "engine",
    "get_session",
    "init_db",
]
