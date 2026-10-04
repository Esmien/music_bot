"""Подключение к базе данных, фабрика сессий и инициализация схемы.

Модуль создаёт async engine SQLAlchemy и sessionmaker для работы с БД.
Драйвер определяется URL: asyncpg для PostgreSQL, aiosqlite для тестов.
expire_on_commit=False позволяет работать с объектами после commit.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings  # type: ignore[attr-defined]
from core.database.models import Base  # type: ignore[attr-defined]

log = logging.getLogger(__name__)

# Драйвер определяется URL: asyncpg для PostgreSQL, aiosqlite для тестов
engine = create_async_engine(settings.db.database_url, echo=False)

# expire_on_commit=False: объекты остаются доступны после commit
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Создаёт сессию базы данных и отдаёт её вызывающему коду.

    Сессия закрывается при выходе из контекстного менеджера,
    в том числе при возникновении исключения.

    Yields:
        AsyncSession: Асинхронная сессия SQLAlchemy.
    """
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Создаёт таблицы по моделям SQLAlchemy, если их ещё нет.

    Идемпотентна: существующие таблицы не изменяются.

    Returns:
        None.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def shutdown_db() -> None:
    """Закрывает движок базы данных и освобождает пул соединений.

    Идемпотентна: повторный вызов безопасен.

    Raises:
        Exception: При ошибке закрытия движка (логируется и пробрасывается).
    """
    try:
        await engine.dispose()
    except Exception as e:
        log.exception("Failed to dispose database engine: %s", e)
        raise
