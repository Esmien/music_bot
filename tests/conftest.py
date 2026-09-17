"""Общая обвязка тестов: детерминированное окружение и тестовая БД.

Переменные окружения задаются жёстко и до любых импортов проекта:
config.py читает и валидирует их прямо на этапе импорта.
"""

import os

os.environ["BOT_TOKEN"] = "test-token"
os.environ["OPENROUTER_API_KEY"] = "test-openrouter-key"
os.environ["BOT_ACCESS_KEY"] = "secret-key"
os.environ["SONG_PRICE"] = "0.5"
os.environ["BOT_OWNER_ID"] = "0"
os.environ["MOCK_MODE"] = "0"

# Импорты проекта — строго после настройки окружения (см. докстринг модуля)
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import database  # noqa: E402
from handlers import auth as handlers_auth  # noqa: E402
from handlers.auth import failed_key_attempts  # noqa: E402
from handlers.state import pending_auth  # noqa: E402


@pytest.fixture
async def db_engine():
    """Реальная in-memory SQLite.

    StaticPool держит один общий коннект, чтобы все сессии теста
    видели одни и те же данные, а не свои копии базы.
    """
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_sessionmaker(db_engine, monkeypatch):
    """Фабрика сессий поверх тестовой БД со схемой из database.init_db().

    Подменяет database.engine, чтобы init_db() собирал схему в тестовой
    базе, а не в боевом sqlite-файле из config.DATABASE_URL.
    """
    monkeypatch.setattr(database, "engine", db_engine)
    await database.init_db()
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def patched_auth_db(db_sessionmaker, monkeypatch):
    """Перенаправляет обращение хендлеров авторизации к тестовой БД."""
    monkeypatch.setattr(handlers_auth, "SessionLocal", db_sessionmaker)
    return db_sessionmaker


@pytest.fixture
def clean_auth_state():
    """Пустые pending_auth и failed_key_attempts до и после теста."""
    pending_auth.clear()
    failed_key_attempts.clear()
    yield
    pending_auth.clear()
    failed_key_attempts.clear()


@pytest.fixture
def make_message():
    """Фабрика сообщений-заглушек: копит ответы бота и факт удаления."""

    class FakeMessage:
        def __init__(self, text=None, uid=1):
            self.text = text
            self.from_user = SimpleNamespace(id=uid)
            self.bot = SimpleNamespace()
            self.answers = []
            self.deleted = False

        async def answer(self, text, **kwargs):
            self.answers.append(text)

        async def delete(self):
            self.deleted = True

    return FakeMessage


@pytest.fixture
def fake_state():
    """Фабрика FSM-контекстов-заглушек, запоминающих вызов clear()."""

    class FakeState:
        def __init__(self):
            self.cleared = False

        async def clear(self):
            self.cleared = True

    return FakeState
