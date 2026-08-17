"""
Alembic environment configuration.

Supports both online (connected) and offline (SQL script generation) modes.
Uses the async SQLAlchemy engine to support asyncpg driver.
Database URL is loaded from environment — never hardcoded.
"""

from logging.config import fileConfig
from typing import Any

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
    # URL and SSL decision both come from Settings — never derived inline.
    # Four modules used to each derive this themselves, which is how the
    # worker modules ended up missing a fix the API had.
    url = settings.sync_database_url
    needs_ssl = settings.requires_ssl

    # connect_timeout is essential, not optional. Without it psycopg2 inherits
    # the OS default (~127s of TCP SYN retries on Linux, or indefinite if the
    # route blackholes), and Alembic emits NO output before a connection
    # succeeds — so an unreachable database looks like a totally silent hang.
    # A container healthcheck will kill the process long before the OS timeout
    # fires, leaving no diagnostic trace at all.
    connect_args: dict[str, Any] = {"connect_timeout": 10}
    if needs_ssl:
        connect_args["sslmode"] = "require"

    safe_host = url.split("@")[1] if "@" in url else url
    print(
        f"[alembic] connecting to {safe_host} "
        f"(sslmode={'require' if needs_ssl else 'disabled'}, timeout=10s)",
        flush=True,
    )

    connectable = create_engine(
        url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with connectable.connect() as connection:
        print("[alembic] connected — running migrations", flush=True)
        do_run_migrations(connection)
        print("[alembic] migrations complete", flush=True)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
