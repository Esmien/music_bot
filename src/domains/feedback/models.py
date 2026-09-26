"""ORM-модели пользовательской обратной связи."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.models import Base

if TYPE_CHECKING:
    from domains.generation.models import Generation


class GenerationFeedback(Base):
    """Оценка и отзыв о генерации.

    Attributes:
        id: Первичный ключ записи обратной связи.
        generation_id: ID генерации, к которой относится обратная связь.
        generation: Связанная генерация.
        is_liked: Понравилась ли пользователю песня.
        feedback: Текстовый отзыв пользователя.
    """

    __tablename__ = "generation_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("generations.id"),
        nullable=False,
        index=True,
    )
    is_liked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    generation: Mapped["Generation"] = relationship(
        "Generation",
        back_populates="feedbacks",
    )
