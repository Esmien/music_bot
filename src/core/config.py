"""Конфигурация проекта: чтение переменных окружения через pydantic-settings.

Все настройки собираются здесь в одном месте — остальные модули
импортируют только этот файл, ничего не читая из окружения напрямую.
"""

from pydantic import computed_field
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseModelConfig(BaseSettings):
    DEV_MODE: bool = False
    # Подхватываем .env из корня проекта при локальном запуске;
    # в Docker переменные приходят через environment/docker-compose
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class BotConfig(BaseModelConfig):
    """Токены и идентификаторы, связанные с ботом и внешними API."""

    BOT_TOKEN: str
    OPENROUTER_API_KEY: str = ""
    MODEL_ID: str = "google/lyria-3-pro-preview"
    BOT_ACCESS_KEY: str = ""
    BOT_OWNER_ID: int = 0


class EnrichPromptConfig(BaseModelConfig):
    """Настройки модели обогащения пользовательского промпта.

    Пустые значения допустимы: обогатитель опционален. При незаданных
    настройках enrich_prompt сигнализирует ValueError, а хендлер
    предлагает продолжить сценарий с исходным описанием песни.
    """

    ENRICH_URL: str = ""
    ENRICH_TOKEN: str = ""
    ENRICH_MODEL: str = ""


class GenerationConfig(BaseModelConfig):
    """Настройки генерации песен.

    SONG_PRICE обязательна: без цены генерации расчёт остатков песен
    невозможен — pydantic упадёт с ValidationError при старте (fail fast).
    """

    SONG_PRICE: float
    MOCK_MODE: bool = False
    MOCK_FILE: str = ""
    TYPICAL_GENERATION_SECONDS: float = 30.0


class DatabaseConfig(BaseModelConfig):
    """Параметры подключения к PostgreSQL.

    asyncpg — асинхронный драйвер, обязательный для SQLAlchemy в async-режиме.
    В Docker переопределяется через docker-compose, aiosqlite остаётся для локальных тестов.
    """

    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_DB: str

    @property
    def postgres_host(self) -> str:
        return "localhost" if self.DEV_MODE else self.POSTGRES_HOST

    @computed_field
    @property
    def database_url(self) -> str:
        url = MultiHostUrl.build(
            scheme="postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.postgres_host,
            port=self.POSTGRES_PORT,
            path=self.POSTGRES_DB,
        )
        return str(url)


class RedisConfig(BaseModelConfig):
    """Redis: хранение FSM-состояний (переживают рестарт контейнера)."""

    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_VAULT: str = "0"

    @property
    def redis_host(self) -> str:
        return "localhost" if self.DEV_MODE else self.REDIS_HOST

    @computed_field
    @property
    def redis_url(self) -> str:
        url = MultiHostUrl.build(
            scheme="redis",
            username=None,
            password=None,
            host=self.redis_host,
            port=self.REDIS_PORT,
            path=f"/{self.REDIS_VAULT}" if not self.REDIS_VAULT.startswith("/") else self.REDIS_VAULT,
        )
        return str(url)


class Settings(BaseModelConfig):
    MIN_FEEDBACK_TEXT: int = 20
    bot: BotConfig = BotConfig()
    generation: GenerationConfig = GenerationConfig()
    db: DatabaseConfig = DatabaseConfig()
    redis: RedisConfig = RedisConfig()
    enrich: EnrichPromptConfig = EnrichPromptConfig()


settings = Settings()
