"""Окружение Alembic: связывает миграции с metadata моделей и DATABASE_URL из config."""

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Корень проекта и src — в начало sys.path, чтобы импортировать config
# и database.models независимо от того, откуда запущен alembic
# (prepend_sys_path из alembic.ini не всегда применяется, например при запуске не из корня)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_PROJECT_ROOT), str(_PROJECT_ROOT / "src")]

import config  # noqa: E402
from database.models import Base  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
# Имя alembic_config, чтобы не затирать модуль config проекта
alembic_config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Запуск миграций без подключения к БД (генерация SQL через --sql).

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=config.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    """Настраивает контекст миграций на переданном синхронном соединении.

    Args:
        connection: Соединение, предоставленное run_sync из async-движка.
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Запуск миграций через async-движок (asyncpg для PostgreSQL)."""
    connectable = async_engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        url=config.DATABASE_URL,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_sync_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Запуск миграций с подключением к БД."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
