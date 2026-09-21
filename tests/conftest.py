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
os.environ["POSTGRES_USER"] = "test-user"
os.environ["POSTGRES_PASSWORD"] = "test-password"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5432"
os.environ["POSTGRES_DB"] = "test-db"

import importlib

# Импорты проекта — строго после настройки окружения (см. докстринг модуля)
from types import SimpleNamespace  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import pytest  # noqa: E402
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import DeleteMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from core.database import init_db
from fsm import evaluation_fsm as handlers_state
from handlers import auth as handlers_auth  # noqa: E402
from handlers.auth import failed_key_attempts  # noqa: E402

# import database.engine as ... вернул бы не модуль, а затенённый атрибут
# пакета database — AsyncEngine (реэкспорт engine в database/__init__.py).
# Поэтому модуль достаём через importlib: он отдаёт запись из sys.modules,
# минуя затенённый атрибут, и db_sessionmaker патчит переменную engine
# именно в database/engine.py
database_engine_module = importlib.import_module("core.database.engine")


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
    # Патчим engine внутри модуля database.engine: init_db замыкается
    # на него, а не на атрибут пакета database (который затенён
    # импортом from .engine import engine в database/__init__.py)
    monkeypatch.setattr(database_engine_module, "engine", db_engine)
    await init_db()
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def patched_auth_db(db_sessionmaker, monkeypatch):
    """Перенаправляет обращение хендлеров авторизации к тестовой БД."""
    monkeypatch.setattr(handlers_auth, "SessionLocal", db_sessionmaker)
    return db_sessionmaker


@pytest.fixture
def fake_redis(monkeypatch):
    """Подменяет клиент Redis в handlers.state на in-memory fakeredis.

    pending_auth теперь живёт в Redis — реальный сервер в тестах не нужен.
    Клиент чист при создании, ключи между тестами не перетекают.
    """
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(handlers_state, "redis_client", client)
    return client


@pytest.fixture
def clean_auth_state(fake_redis):
    """Пустой pending_auth (свежий fakeredis) и failed_key_attempts до и после теста."""
    failed_key_attempts.clear()
    yield
    failed_key_attempts.clear()


@pytest.fixture
def make_message():
    """Фабрика сообщений-заглушек для хендлеров генерации и авторизации.

    FakeMessage копит текстовые ответы (answers), отправленные сообщения
    с историей правок (sent — нужно для проверки прогресс-бара), аудио,
    факт удаления и chat actions. fail_delete имитирует сбой удаления
    сообщения (например, недостаточно прав у бота).
    """

    class FakeBot:
        def __init__(self):
            self.chat_actions = []

        async def send_chat_action(self, chat_id, action):
            self.chat_actions.append(action)

    class FakeMessage:
        def __init__(self, text=None, uid=1):
            self.text = text
            self.from_user = SimpleNamespace(id=uid, username=f"user_{uid}")
            self.chat = SimpleNamespace(id=uid)
            self.bot = FakeBot()
            self.answers = []
            self.sent = []
            self.audios = []
            self.deleted = False
            self.fail_delete = False

        async def answer(self, text, **kwargs):
            self.answers.append(text)
            sent = SimpleNamespace(text=text, edits=[], deleted=False)

            async def edit_text(new_text, **kw):
                sent.text = new_text
                sent.edits.append(new_text)
                return sent

            async def delete():
                sent.deleted = True

            sent.edit_text = edit_text
            sent.delete = delete
            self.sent.append(sent)
            return sent

        async def answer_audio(self, audio, **kwargs):
            self.audios.append(audio)

        async def delete(self):
            if self.fail_delete:
                raise TelegramAPIError(method=DeleteMessage(chat_id=0, message_id=0), message="delete failed")
            self.deleted = True

    return FakeMessage


@pytest.fixture
def fake_state():
    """Фабрика FSM-контекстов-заглушек с хранилищем данных в памяти.

    Помимо факта clear() запоминает текущее состояние (set_state) и
    держит словарь данных (get_data/update_data), как настоящий FSMContext.
    """

    class FakeState:
        def __init__(self):
            self.cleared = False
            self.state = None
            self._data = {}

        async def clear(self):
            self.cleared = True
            self._data.clear()

        async def get_data(self):
            return dict(self._data)

        async def update_data(self, **kwargs):
            self._data.update(kwargs)

        async def set_state(self, state):
            self.state = state

    return FakeState
