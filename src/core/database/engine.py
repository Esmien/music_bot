"""Подключение к базе данных, фабрика сессий и инициализация схемы."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from core.database.models import Base

# Драйвер определяется URL: PostgreSQL (asyncpg) в бою,
# aiosqlite — в тестах с in-memory БД
engine = create_async_engine(settings.db.database_url, echo=False)
# expire_on_commit=False: объекты остаются пригодны после commit —
# иначе ленивое обращение к атрибутам ломалось бы в async-контексте
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Создаёт сессию базы данных и отдаёт её вызывающему коду.

    Сессия закрывается при выходе из контекстного менеджера, в том числе
    если во время её использования возникает исключение.

    Yields:
        AsyncSession: Асинхронная сессия SQLAlchemy.
    """
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Создаёт таблицы по моделям SQLAlchemy, если их ещё нет.

    Идемпотентна: существующие таблицы не трогаются,
    поэтому её безопасно вызывать при каждом старте бота.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
