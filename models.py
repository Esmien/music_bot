"""ORM-модели SQLAlchemy."""

from sqlalchemy import Boolean, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
