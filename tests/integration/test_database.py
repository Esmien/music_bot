"""Интеграционные тесты БД: реальные таблицы в in-memory SQLite."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import database
from models import User

pytestmark = pytest.mark.integration


async def test_user_roundtrip(db_sessionmaker):
    async with db_sessionmaker() as session:
        session.add(User(tg_id=7, is_authorized=True))
        await session.commit()

    async with db_sessionmaker() as session:
        user = (await session.execute(select(User).where(User.tg_id == 7))).scalar_one()
        assert user.is_authorized is True


async def test_duplicate_tg_id_rejected(db_sessionmaker):
    async with db_sessionmaker() as session:
        session.add(User(tg_id=1000))
        await session.commit()

    async with db_sessionmaker() as session:
        session.add(User(tg_id=1000))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_init_db_is_idempotent(db_sessionmaker):
    # Схема уже создана фикстурой — повторный прогон init_db не должен падать
    await database.init_db()
