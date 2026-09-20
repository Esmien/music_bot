"""ORM-модели SQLAlchemy."""

from sqlalchemy import Boolean, ForeignKey, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей проекта."""

    pass


class User(Base):
    """Пользователь бота.

    Attributes:
        id: Суррогатный первичный ключ.
        tg_id: Telegram user_id — уникален и индексирован,
            т.к. по нему идут все поиски пользователя.
        is_authorized: Прошёл ли пользователь вход по ключу доступа.
    """

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)


class GenerationFeedback(Base):
    """Отзыв пользователя о сгенерированной песне.

    Attributes:
        id: Суррогатный первичный ключ.
        user_id: Telegram user_id пользователя (FK на users.tg_id).
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
    is_liked: Mapped[bool] = mapped_column(Boolean)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User")
