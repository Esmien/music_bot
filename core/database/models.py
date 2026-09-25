"""Общая база SQLAlchemy и совместимые реэкспорты доменных моделей."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс для ORM-моделей проекта."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


# Реэкспорт сохраняет совместимость старых импортов.
# Сами модели определены в соответствующих доменах.
from domains.base.models import User  # noqa: E402
from domains.feedback.models import GenerationFeedback  # noqa: E402
from domains.generation.models import Generation, GenerationStatus  # noqa: E402

__all__ = ["Base", "Generation", "GenerationFeedback", "GenerationStatus", "User"]
