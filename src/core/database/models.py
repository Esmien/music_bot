"""ORM-модели SQLAlchemy."""

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей проекта."""

    pass


class User(Base):
    """Пользователь бота.

    Attributes:
        tg_id: Telegram user_id — уникален и индексирован,
            т.к. по нему идут все поиски пользователя.
        is_authorized: Прошёл ли пользователь вход по ключу доступа.
        feedbacks: Отзывы пользователя о сгенерированных песнях.
    """

    __tablename__ = "users"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)

    feedbacks: Mapped[list["GenerationFeedback"]] = relationship(back_populates="user")


class GenerationFeedback(Base):
    """Отзыв пользователя о сгенерированной песне.

    Attributes:
        id: Суррогатный первичный ключ.
        user_id: ID пользователя (FK на users.id).
        user: Связанный объект User.
        initial_prompt: Промпт от пользователя.
        enriched_prompt: Обработанный ИИ промпт.
        is_liked: Понравилась ли пользователю сгенерированная песня.
        feedback: Опциональное короткое резюме пользователя о песне.
    """

    __tablename__ = "generation_feedbacks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.tg_id"), index=True)
    initial_prompt: Mapped[str] = mapped_column(Text)
    enriched_prompt: Mapped[str] = mapped_column(Text)
    is_liked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="feedbacks")
