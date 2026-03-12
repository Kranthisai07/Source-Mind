"""
Alembic environment configuration.

Supports both online (connected) and offline (SQL script generation) modes.
Uses the async SQLAlchemy engine to support asyncpg driver.
Database URL is loaded from environment — never hardcoded.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import all models so that Base.metadata is fully populated
# This is required for autogenerate to detect all tables
from sourcemind.models import Base  # noqa: F401 — registers all models
from sourcemind.core.config import get_settings

# Alembic Config object (provides access to alembic.ini values)
config = context.config

# Set up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Provide the metadata for autogenerate support
target_metadata = Base.metadata

# Load database URL from environment (never from alembic.ini)
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def include_object(object: object, name: str, type_: str, reflected: bool, compare_to: object) -> bool:
    """
    Filter which objects are included in autogenerate comparisons.

    Excludes tables managed by external systems (e.g. Clerk auth tables).
    """
    if type_ == "table" and name in {"spatial_ref_sys"}:
        return False
    return True


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL without a live connection.

    Used for generating migration scripts to review before applying.
    Run: alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations with a live database connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        # Use transactional DDL (wraps each migration in a transaction)
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Async entry point for online migrations."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # Don't pool connections during migrations
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database (standard invocation)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
