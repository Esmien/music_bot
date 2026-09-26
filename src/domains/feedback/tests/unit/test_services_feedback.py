"""Тесты сервиса сохранения обратной связи."""

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.database.models import User
from domains.feedback import service as feedback_service
from domains.feedback.models import GenerationFeedback
from domains.generation.models import Generation, GenerationStatus


@pytest.fixture
def patched_feedback_db(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> async_sessionmaker[AsyncSession]:
    """Подменяет get_session сервиса обратной связи на тестовую БД."""
    monkeypatch.setattr(feedback_service, "get_session", db_sessionmaker)
    return db_sessionmaker


async def _create_generation(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
    *,
    create_feedback: bool = False,
    is_liked: bool | None = False,
    feedback: str | None = None,
) -> tuple[Generation, GenerationFeedback | None]:
    """Создаёт пользователя, успешную генерацию и при необходимости её оценку."""
    async with sessionmaker() as session:
        user = await session.get(User, user_id)
        if user is None:
            session.add(User(tg_id=user_id, is_authorized=True))
            await session.flush()

        generation = Generation(
            user_id=user_id,
            prompt="исходный промпт",
            enriched_prompt={"text": "обогащённый промпт"},
            title="Песня",
            status=GenerationStatus.SUCCESS,
        )
        session.add(generation)
        await session.flush()

        feedback_record = None
        if create_feedback:
            feedback_record = GenerationFeedback(
                generation_id=generation.id,
                is_liked=is_liked,
                feedback=feedback,
            )
            session.add(feedback_record)

        await session.commit()
        return generation, feedback_record


async def _get_feedback_records(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: int,
) -> list[GenerationFeedback]:
    """Возвращает записи обратной связи пользователя в порядке генераций."""
    async with sessionmaker() as session:
        result = await session.execute(
            select(GenerationFeedback)
            .join(Generation, GenerationFeedback.generation_id == Generation.id)
            .where(Generation.user_id == user_id)
            .order_by(Generation.id.asc())
        )
        return list(result.scalars().all())


async def test_save_feedback_skips_without_touching_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой отзыв и дизлайк не должны создавать сессию БД."""

    def _fail_get_session() -> None:
        raise AssertionError("get_session should not be called")

    monkeypatch.setattr(feedback_service, "get_session", _fail_get_session)

    await feedback_service.save_feedback(user_id=1, feedback=None, evalue=False)
    await feedback_service.save_feedback(user_id=1, feedback="", evalue=False)


async def test_save_feedback_creates_record_for_like_only(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Лайк без текста создаёт запись обратной связи для успешной генерации."""
    generation, _ = await _create_generation(sessionmaker=patched_feedback_db, user_id=1)

    await feedback_service.save_feedback(user_id=1, feedback=None, evalue=True)

    records = await _get_feedback_records(sessionmaker=patched_feedback_db, user_id=1)

    assert len(records) == 1
    assert records[0].generation_id == generation.id
    assert records[0].is_liked is True
    assert records[0].feedback is None


async def test_save_feedback_creates_record_for_text_and_dislike(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Текст отзыва сохраняется даже при отрицательной оценке."""
    generation, _ = await _create_generation(sessionmaker=patched_feedback_db, user_id=1)

    await feedback_service.save_feedback(user_id=1, feedback="Слишком громко", evalue=False)

    records = await _get_feedback_records(sessionmaker=patched_feedback_db, user_id=1)

    assert len(records) == 1
    assert records[0].generation_id == generation.id
    assert records[0].is_liked is False
    assert records[0].feedback == "Слишком громко"


async def test_save_feedback_overwrites_existing_feedback_text(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Повторная отправка отзыва обновляет оценку и текст той же генерации."""
    generation, original_record = await _create_generation(
        sessionmaker=patched_feedback_db,
        user_id=1,
        create_feedback=True,
        is_liked=False,
        feedback="bad",
    )

    await feedback_service.save_feedback(user_id=1, feedback="good", evalue=True)

    records = await _get_feedback_records(sessionmaker=patched_feedback_db, user_id=1)

    assert len(records) == 1
    assert original_record is not None
    assert records[0].id == original_record.id
    assert records[0].generation_id == generation.id
    assert records[0].is_liked is True
    assert records[0].feedback == "good"


async def test_save_feedback_updates_latest_record_for_user(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Оценка обновляет запись последней генерации пользователя."""
    old_generation, old_record = await _create_generation(
        sessionmaker=patched_feedback_db,
        user_id=1,
        create_feedback=True,
        is_liked=False,
    )
    latest_generation, latest_record = await _create_generation(
        sessionmaker=patched_feedback_db,
        user_id=1,
        create_feedback=True,
        is_liked=False,
    )

    await feedback_service.save_feedback(user_id=1, feedback="Хорошо", evalue=True)

    async with patched_feedback_db() as session:
        refreshed_old = await session.get(GenerationFeedback, old_record.id)
        refreshed_latest = await session.get(GenerationFeedback, latest_record.id)

    assert old_generation.id != latest_generation.id
    assert refreshed_old is not None
    assert refreshed_latest is not None
    assert refreshed_old.is_liked is False
    assert refreshed_old.feedback is None
    assert refreshed_latest.is_liked is True
    assert refreshed_latest.feedback == "Хорошо"


async def test_save_feedback_does_not_update_other_user(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Сохранение оценки пользователя не меняет запись другого пользователя."""
    _, other_record = await _create_generation(
        sessionmaker=patched_feedback_db,
        user_id=2,
        create_feedback=True,
        is_liked=False,
    )
    await _create_generation(sessionmaker=patched_feedback_db, user_id=1)

    await feedback_service.save_feedback(user_id=1, feedback="Моя оценка", evalue=True)

    async with patched_feedback_db() as session:
        refreshed_other = await session.get(GenerationFeedback, other_record.id)

    my_records = await _get_feedback_records(sessionmaker=patched_feedback_db, user_id=1)

    assert refreshed_other is not None
    assert refreshed_other.is_liked is False
    assert refreshed_other.feedback is None
    assert len(my_records) == 1
    assert my_records[0].is_liked is True
    assert my_records[0].feedback == "Моя оценка"


async def test_save_feedback_does_not_create_record_without_successful_generation(
    patched_feedback_db: async_sessionmaker[AsyncSession],
) -> None:
    """Оценка не создаёт запись, если у пользователя нет успешной генерации."""
    async with patched_feedback_db() as session:
        session.add(User(tg_id=1, is_authorized=True))
        session.add(
            Generation(
                user_id=1,
                prompt="исходный промпт",
                enriched_prompt={"text": "обогащённый промпт"},
                status=GenerationStatus.PENDING,
            )
        )
        await session.commit()

    await feedback_service.save_feedback(user_id=1, feedback="Отзыв", evalue=True)

    assert await _get_feedback_records(sessionmaker=patched_feedback_db, user_id=1) == []


async def test_save_feedback_swallows_and_logs_sqlalchemy_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Сбой БД логируется и не пробрасывается наружу."""

    class FailingSession:
        async def __aenter__(self) -> "FailingSession":
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> None:
            return None

        async def execute(self, *args: object, **kwargs: object) -> None:
            raise SQLAlchemyError("db error")

    monkeypatch.setattr(feedback_service, "get_session", lambda: FailingSession())
    caplog.set_level(logging.ERROR, logger=feedback_service.log.name)

    await feedback_service.save_feedback(user_id=1, feedback="test", evalue=True)

    assert "Failed to save feedback" in caplog.text
    assert "user=1" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)
