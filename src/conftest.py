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
os.environ["ENRICH_URL"] = "https://enricher.test/api/v1/chat/completions"
os.environ["ENRICH_TOKEN"] = "test-enrich-token"
os.environ["ENRICH_MODEL"] = "test-enrich-model"
os.environ["RABBITMQ_USER"] = "test-user"
os.environ["RABBITMQ_PASSWORD"] = "test-password"
os.environ["RABBITMQ_URL"] = ""  # Пустой URL для тестов — используем InMemoryBroker

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
from domains.auth import service as auth_service
from domains.base import service as base_service

# import database.engine as ... вернул бы не модуль, а затенённый атрибут
# пакета database — AsyncEngine (реэкспорт engine в database/__init__.py).
# Поэтому модуль достаём через importlib: он отдаёт запись из sys.modules,
# минуя затенённый атрибут, и db_sessionmaker патчит переменную engine
# именно в database/engine.py
database_engine_module = importlib.import_module("core.database.engine")


@pytest.fixture(autouse=True)
async def db_engine():
    """Реальная in-memory SQLite.

    StaticPool держит один общий коннект, чтобы все сессии теста
    видели одни и те же данные, а не свои копии базы.
    """
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
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


@pytest.fixture(autouse=True)
async def patched_auth_db(db_sessionmaker, monkeypatch):
    """Перенаправляет сервисы авторизации и base на тестовую SQLite."""
    monkeypatch.setattr(auth_service, "get_session", db_sessionmaker)
    monkeypatch.setattr(base_service, "get_session", db_sessionmaker)
    return db_sessionmaker


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Подменяет клиент Redis в сервисе авторизации на in-memory fakeredis.

    Реальный сервер в тестах не нужен. Клиент чист при создании,
    ключи между тестами не перетекают.
    """
    import core.redis as redis_module
    from domains.auth.registries import auth_registry
    from domains.generation import service as generation_service

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)

    # Патчим redis_client в core.redis — источник всех импортов
    monkeypatch.setattr(redis_module, "redis_client", client)
    monkeypatch.setattr(auth_registry, "redis_client", client)
    monkeypatch.setattr(generation_service, "redis_client", client)

    return client


@pytest.fixture(autouse=True)
def clean_auth_state(fake_redis):
    """Чистый реестр авторизации.

    И pending_auth, и счётчик неудачных попыток живут в Redis;
    fake_redis создаётся заново на каждый тест, поэтому ключи не
    перетекают между тестами.
    """
    yield fake_redis


@pytest.fixture(autouse=True)
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
            # bot нужен хендлерам для notify_owner (как у реального aiogram.Message)
            sent = SimpleNamespace(text=text, edits=[], deleted=False, bot=self.bot)

            # Хендлеры зовут edit_text(text=...) именованным аргументом
            async def edit_text(text, **kw):
                sent.text = text
                sent.edits.append(text)
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


@pytest.fixture(autouse=True)
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


@pytest.fixture(autouse=True)
def inmemory_broker(monkeypatch):
    """InMemoryBroker для тестов — задачи выполняются синхронно в процессе.

    Подменяет все брокеры из core.broker на InMemoryBroker,
    сохраняя существующий стиль интеграционных тестов.
    """
    from core import broker as broker_module

    test_broker = broker_module._create_inmemory_broker()

    # Подменяем все брокеры на in-memory версию
    monkeypatch.setattr(broker_module, "enricher_broker", test_broker)
    monkeypatch.setattr(broker_module, "generation_broker", test_broker)
    monkeypatch.setitem(broker_module.brokers, "enricher", test_broker)
    monkeypatch.setitem(broker_module.brokers, "generation", test_broker)

    return test_broker


@pytest.fixture
def fake_telegram_port():
    """Фейковый TelegramPort для тестирования воркеров без aiogram.

    Сохраняет историю всех вызовов методов порта для последующей проверки.
    Используется в integration-тестах воркеров.
    """
    from shared.ports.fake_telegram import FakeTelegramPort

    return FakeTelegramPort()


@pytest.fixture(autouse=True)
def track_aiohttp_sessions(monkeypatch):
    """Отслеживает создание aiohttp.ClientSession для обнаружения утечек.

    Этот fixture проверяет, что все созданные сессии были корректно закрыты.
    Используется для валидации отсутствия утечек в event-хендлерах воркеров.
    """
    import aiohttp

    original_init = aiohttp.ClientSession.__init__
    original_close = aiohttp.ClientSession.close
    created_sessions = []
    closed_sessions = []

    def tracked_init(self, *args, **kwargs):
        created_sessions.append(id(self))
        return original_init(self, *args, **kwargs)

    async def tracked_close(self):
        closed_sessions.append(id(self))
        return await original_close(self)

    monkeypatch.setattr(aiohttp.ClientSession, "__init__", tracked_init)
    monkeypatch.setattr(aiohttp.ClientSession, "close", tracked_close)

    yield {"created": created_sessions, "closed": closed_sessions}

    # Проверка на утечки после теста
    leaked = set(created_sessions) - set(closed_sessions)
    if leaked:
        import warnings

        warnings.warn(f"Detected {len(leaked)} unclosed aiohttp sessions: {leaked}", ResourceWarning, stacklevel=2)
