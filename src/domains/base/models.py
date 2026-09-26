"""ORM-модели домена базовых пользовательских сущностей."""

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.models import Base

if TYPE_CHECKING:
    from domains.feedback.models import GenerationFeedback
    from domains.generation.models import Generation


class User(Base):
    """Пользователь бота.

    Attributes:
        tg_id: Telegram user_id пользователя.
        is_authorized: Признак авторизации пользователя.
        generations: Генерации пользователя.
        feedbacks: Оценки и отзывы пользователя.
    """

    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    generations: Mapped[list["Generation"]] = relationship(
        "Generation",
        back_populates="user",
    )
    feedbacks: Mapped[list["GenerationFeedback"]] = relationship(
        "GenerationFeedback",
        secondary="generations",
        primaryjoin="User.tg_id == Generation.user_id",
        secondaryjoin="Generation.id == GenerationFeedback.generation_id",
        viewonly=True,
    )
