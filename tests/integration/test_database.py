"""Интеграционные тесты БД: реальные таблицы в in-memory SQLite."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import database
from models import User

pytestmark = pytest.mark.integration


async def test_user_roundtrip(db_sessionmaker):
    """Запись User сохраняется и читается обратно с теми же полями."""
    async with db_sessionmaker() as session:
        session.add(User(tg_id=7, is_authorized=True))
        await session.commit()

    async with db_sessionmaker() as session:
        user = (await session.execute(select(User).where(User.tg_id == 7))).scalar_one()
        assert user.is_authorized is True


async def test_duplicate_tg_id_rejected(db_sessionmaker):
    """Один tg_id не может встречаться дважды — уникальность на уровне БД."""
    async with db_sessionmaker() as session:
        session.add(User(tg_id=1000))
        await session.commit()

    async with db_sessionmaker() as session:
        session.add(User(tg_id=1000))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_init_db_is_idempotent(db_sessionmaker):
    """Повторный вызов init_db() не падает и не трогает существующие таблицы.

    Схема уже создана фикстурой db_sessionmaker.
    """
    await database.init_db()
