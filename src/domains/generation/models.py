"""ORM-модели сущностей генерации."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database.models import Base

if TYPE_CHECKING:
    from domains.base.models import User
    from domains.feedback.models import GenerationFeedback


class GenerationStatus(StrEnum):
    """Статус жизненного цикла генерации."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Generation(Base):
    """Генерация песни и связанные с ней исходные данные.

    Attributes:
        id: Первичный ключ генерации.
        prompt: Исходный промпт пользователя.
        enriched_prompt: Обогащённый промпт в структурированном виде.
        title: Название песни.
        created_at: Время создания записи.
        status: Статус генерации.
        user_id: Telegram user_id владельца генерации.
        user: Пользователь, которому принадлежит генерация.
        feedbacks: Оценки и отзывы о генерации.
    """

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    enriched_prompt: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    status: Mapped[GenerationStatus] = mapped_column(
        Enum(
            GenerationStatus,
            name="generation_status",
            native_enum=False,
            length=16,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=GenerationStatus.PENDING,
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.tg_id"),
        nullable=False,
        index=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="generations",
    )
    feedbacks: Mapped[list["GenerationFeedback"]] = relationship(
        "GenerationFeedback",
        back_populates="generation",
    )
