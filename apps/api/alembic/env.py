"""
Alembic environment configuration.

Supports both online (connected) and offline (SQL script generation) modes.
Uses the async SQLAlchemy engine to support asyncpg driver.
Database URL is loaded from environment — never hardcoded.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

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
# configparser uses % for interpolation — escape literal % chars in the URL
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))


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


def run_migrations_online() -> None:
    """Run migrations against a live database using synchronous psycopg2 driver."""
    # Swap asyncpg → psycopg2 for the migration runner only.
    # The app still uses asyncpg at runtime.
    # Railway's private network does not terminate TLS, so requiring SSL there
    # fails with "server does not support SSL". Public proxies and Supabase do.
    original_url = settings.database_url
    needs_ssl = (
        "ssl=require" in original_url or ".railway.internal" not in original_url
    )

    url = (
        original_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("?ssl=require", "")
        .replace("&ssl=require", "")
    )

    connect_args = {"sslmode": "require"} if needs_ssl else {}

    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
