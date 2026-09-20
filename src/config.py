"""Конфигурация проекта: чтение переменных окружения через pydantic-settings.

Все настройки собираются здесь в одном месте — остальные модули
импортируют только этот файл, ничего не читая из окружения напрямую.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseModelConfig(BaseSettings):
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


class GenerationConfig(BaseModelConfig):
    """Настройки генерации песен.

    SONG_PRICE обязательна: без цены генерации расчёт остатков песен
    невозможен — pydantic упадёт с ValidationError при старте (fail fast).
    """

    SONG_PRICE: float
    MOCK_MODE: bool = False
    MOCK_FILE: str = ""


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
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


class RedisConfig(BaseModelConfig):
    """Redis: хранение FSM-состояний (переживают рестарт контейнера)."""

    REDIS_URL: str = "redis://localhost:6379/0"


class Settings(BaseModelConfig):
    bot: BotConfig = BotConfig()
    generation: GenerationConfig = GenerationConfig()
    db: DatabaseConfig = DatabaseConfig()
    redis: RedisConfig = RedisConfig()


settings = Settings()
