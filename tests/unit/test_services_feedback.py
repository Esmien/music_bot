"""Юнит-тесты сервиса сохранения обратной связи."""

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.database.models import GenerationFeedback
from services import feedback as feedback_service


@pytest.fixture
def patched_feedback_db(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> async_sessionmaker[AsyncSession]:
    """Подменяет SessionLocal сервиса обратной связи на тестовую БД."""
    monkeypatch.setattr(feedback_service, "SessionLocal", db_sessionmaker)
    return db_sessionmaker


async def _get_feedback_records(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
) -> list[GenerationFeedback]:
    """Возвращает все записи обратной связи пользователя в порядке создания."""
    async with sessionmaker() as session:
        result = await session.execute(
            select(GenerationFeedback)
            .where(GenerationFeedback.user_id == user_id)
            .order_by(GenerationFeedback.id.asc())
        )
        return list(result.scalars().all())


async def _create_feedback_record(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    initial_prompt: str,
    enriched_prompt: str,
    title: str | None,
    is_liked: bool = False,
    feedback: str | None = None,
) -> GenerationFeedback:
    """Создаёт запись обратной связи напрямую в тестовой БД."""
    record = GenerationFeedback(
        user_id=user_id,
        initial_prompt=initial_prompt,
        enriched_prompt=enriched_prompt,
        title=title,
        is_liked=is_liked,
        feedback=feedback,
    )
    async with sessionmaker() as session:
        session.add(record)
        await session.commit()
    return record


async def test_save_feedback_skips_without_touching_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой отзыв и дизлайк не должны создавать сессию БД."""

    def _fail_session_local() -> None:
        raise AssertionError("SessionLocal should not be called")

    monkeypatch.setattr(feedback_service, "SessionLocal", _fail_session_local)

    await feedback_service.save_feedback(user_id=1, feedback=None, evalue=False)
    await feedback_service.save_feedback(user_id=1, feedback="", evalue=False)


async def test_save_feedback_creates_minimal_record_for_like_only(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Лайк без текста создаёт минимальную запись с пустыми промптами."""
    await feedback_service.save_feedback(user_id=1, feedback=None, evalue=True)

    records = await _get_feedback_records(patched_feedback_db, user_id=1)

    assert len(records) == 1
    record = records[0]
    assert record.user_id == 1
    assert record.is_liked is True
    assert record.feedback is None
    assert record.initial_prompt == ""
    assert record.enriched_prompt == ""
    assert record.title is None


async def test_save_feedback_creates_record_for_text_and_dislike(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Текст отзыва сохраняется даже при отрицательной оценке."""
    await feedback_service.save_feedback(user_id=1, feedback="Слишком громко", evalue=False)

    records = await _get_feedback_records(patched_feedback_db, user_id=1)

    assert len(records) == 1
    record = records[0]
    assert record.is_liked is False
    assert record.feedback == "Слишком громко"


async def test_save_feedback_overwrites_existing_feedback_text(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Повторная отправка отзыва обновляет последнюю запись."""
    await _create_feedback_record(
        sessionmaker=patched_feedback_db,
        user_id=1,
        initial_prompt="first",
        enriched_prompt="first",
        title="first",
        is_liked=False,
        feedback="bad",
    )

    await feedback_service.save_feedback(user_id=1, feedback="good", evalue=True)

    records = await _get_feedback_records(patched_feedback_db, user_id=1)

    assert len(records) == 1
    assert records[0].is_liked is True
    assert records[0].feedback == "good"
    assert records[0].initial_prompt == "first"


async def test_save_feedback_updates_latest_record_for_user(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Оценка обновляет последнюю запись конкретного пользователя."""
    old_record = await _create_feedback_record(
        sessionmaker=patched_feedback_db,
        user_id=1,
        initial_prompt="old",
        enriched_prompt="old",
        title="old",
    )
    latest_record = await _create_feedback_record(
        sessionmaker=patched_feedback_db,
        user_id=1,
        initial_prompt="latest",
        enriched_prompt="latest",
        title="latest",
    )

    await feedback_service.save_feedback(user_id=1, feedback="Хорошо", evalue=True)

    records = await _get_feedback_records(patched_feedback_db, user_id=1)
    assert len(records) == 2

    async with patched_feedback_db() as session:
        refreshed_old = await session.get(GenerationFeedback, old_record.id)
        refreshed_latest = await session.get(GenerationFeedback, latest_record.id)

    assert refreshed_old is not None
    assert refreshed_latest is not None
    assert refreshed_old.is_liked is False
    assert refreshed_old.feedback is None
    assert refreshed_latest.is_liked is True
    assert refreshed_latest.feedback == "Хорошо"
    assert refreshed_latest.initial_prompt == "latest"
    assert refreshed_latest.enriched_prompt == "latest"
    assert refreshed_latest.title == "latest"


async def test_save_feedback_does_not_update_other_user(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранение не должно трогать записи других пользователей."""
    other_record = await _create_feedback_record(
        sessionmaker=patched_feedback_db,
        user_id=2,
        initial_prompt="other",
        enriched_prompt="other",
        title="other",
    )

    await feedback_service.save_feedback(user_id=1, feedback="Моя оценка", evalue=True)

    async with patched_feedback_db() as session:
        refreshed_other = await session.get(GenerationFeedback, other_record.id)
        my_records_result = await session.execute(select(GenerationFeedback).where(GenerationFeedback.user_id == 1))

    assert refreshed_other is not None
    assert refreshed_other.is_liked is False
    assert refreshed_other.feedback is None
    assert my_records_result.scalar_one_or_none() is not None


async def test_save_feedback_swallows_and_logs_sqlalchemy_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Сбой БД логируется и не пробрасывается наружу."""

    class FakeResult:
        def scalar_one_or_none(self) -> GenerationFeedback | None:
            return None

    class FailingCommitSession:
        async def __aenter__(self) -> "FailingCommitSession":
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> None:
            return None

        async def execute(self, *args: object, **kwargs: object) -> FakeResult:
            return FakeResult()

        def add(self, obj: GenerationFeedback) -> None:
            return None

        async def commit(self) -> None:
            raise SQLAlchemyError("db error")

    monkeypatch.setattr(feedback_service, "SessionLocal", lambda: FailingCommitSession())
    caplog.set_level(logging.ERROR, logger=feedback_service.log.name)

    await feedback_service.save_feedback(user_id=1, feedback="test", evalue=True)

    assert "Failed to save feedback" in caplog.text
    assert "user=1" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)
