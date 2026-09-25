"""Интеграционные тесты сохранения обогащённого промпта в БД.

Используется реальная in-memory SQLite (см. conftest.py), внешние API
не задействуются: save_enriched_prompt работает только с БД.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core.database.models import User
from domains.enricher import service as enricher
from domains.generation.models import Generation, GenerationStatus


@pytest.fixture
def patched_enricher_db(db_sessionmaker, monkeypatch):
    """Перенаправляет обращение сервиса обогатителя к тестовой БД."""
    monkeypatch.setattr(enricher, "get_session", db_sessionmaker)
    return db_sessionmaker


async def _create_user(sessionmaker, tg_id: int) -> None:
    """Создаёт пользователя в тестовой БД."""
    async with sessionmaker() as session:
        session.add(User(tg_id=tg_id, is_authorized=True))
        await session.commit()


async def test_save_enriched_prompt_creates_record(patched_enricher_db):
    """Обогащение создаёт ожидающую генерацию с промптами."""
    await _create_user(patched_enricher_db, tg_id=42)

    await enricher.save_enriched_prompt(tg_id=42, initial_prompt="идея песни", enriched_prompt="обогащённый промпт")

    async with patched_enricher_db() as session:
        record = await session.scalar(select(Generation).where(Generation.user_id == 42))

    assert record is not None
    assert record.prompt == "идея песни"
    assert record.enriched_prompt == {"text": "обогащённый промпт"}
    assert record.status == GenerationStatus.PENDING
    assert record.title is None


async def test_save_enriched_prompt_keeps_history_per_user(patched_enricher_db):
    """История генераций хранится отдельно для каждого пользователя."""
    await _create_user(patched_enricher_db, tg_id=1)
    await _create_user(patched_enricher_db, tg_id=2)

    await enricher.save_enriched_prompt(tg_id=1, initial_prompt="первая идея", enriched_prompt="первый промпт")
    await enricher.save_enriched_prompt(tg_id=1, initial_prompt="вторая идея", enriched_prompt="второй промпт")
    await enricher.save_enriched_prompt(tg_id=2, initial_prompt="чужая идея", enriched_prompt="чужой промпт")

    async with patched_enricher_db() as session:
        user_records = (await session.scalars(select(Generation).where(Generation.user_id == 1))).all()
        other_records = (await session.scalars(select(Generation).where(Generation.user_id == 2))).all()

    assert len(user_records) == 2
    assert {record.prompt for record in user_records} == {"первая идея", "вторая идея"}
    assert {record.enriched_prompt["text"] for record in user_records} == {"первый промпт", "второй промпт"}
    assert len(other_records) == 1
    assert other_records[0].prompt == "чужая идея"
    assert other_records[0].enriched_prompt == {"text": "чужой промпт"}


async def test_save_enriched_prompt_db_error_propagates(monkeypatch):
    """Ошибка записи в БД передаётся вызывающему коду."""

    class FailingSession:
        """Сессия-заглушка, имитирующая сбой БД на commit."""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def add(self, obj):
            return None

        async def commit(self):
            raise SQLAlchemyError("commit failed")

    monkeypatch.setattr(enricher, "get_session", FailingSession)

    with pytest.raises(SQLAlchemyError, match="commit failed"):
        await enricher.save_enriched_prompt(tg_id=1, initial_prompt="идея", enriched_prompt="промпт")
