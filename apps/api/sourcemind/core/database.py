"""
Async SQLAlchemy database connection management.

Uses asyncpg driver with connection pooling. Never use sync SQLAlchemy
in any async context — all queries must go through async sessions.
pgvector extension is registered on engine creation.
"""

from collections.abc import AsyncGenerator
from typing import Any

import structlog
from pgvector.sqlalchemy import Vector  # noqa: F401 — registers type globally
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sourcemind.core.config import get_settings

logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine(settings: Any = None) -> AsyncEngine:
    """
    Construct the async SQLAlchemy engine.

    Pooling is left at the SQLAlchemy async default
    (AsyncAdaptedQueuePool), tuned by the pool_* settings passed below.

    This previously computed a `pool_class` of NullPool for development
    and QueuePool otherwise, and never passed it to create_async_engine.
    The selection had no effect for as long as it existed. It is not
    wired up here rather than deleted-and-forgotten because NullPool
    rejects the pool_size / max_overflow / pool_timeout arguments below,
    so enabling it is a real change with its own testing, not a one-line
    fix.
    """
    if settings is None:
        settings = get_settings()

    # asyncpg does not support ?ssl=require as a query param — strip it and
    # pass via connect_args instead.
    # statement_cache_size=0 is required for Supabase Supavisor (session pooler):
    # the pooler can route connections across backends, invalidating prepared
    # statement caches and causing "prepared statement does not exist" errors.
    # Always via settings.async_database_url — never derive the URL inline.
    # See the note on Settings.async_database_url for why.
    db_url = settings.async_database_url
    connect_args: dict[str, Any] = {"statement_cache_size": 0}
    if "supabase" in db_url or "pooler" in db_url:
        connect_args["ssl"] = "require"

    engine = create_async_engine(
        db_url,
        echo=settings.debug,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_recycle=settings.database_pool_recycle,
        pool_pre_ping=True,
        connect_args=connect_args,
        # Use orjson for faster JSON serialization
        json_serializer=_json_serializer,
        json_deserializer=_json_deserializer,
    )

    return engine


def _json_serializer(obj: Any) -> str:
    """Use orjson for performance."""
    import orjson

    return orjson.dumps(obj).decode()


def _json_deserializer(s: str) -> Any:
    """Use orjson for performance."""
    import orjson

    return orjson.loads(s)


async def init_db() -> None:
    """
    Initialize database engine and session factory.
    Called once at application startup via lifespan.
    """
    global _engine, _session_factory

    settings = get_settings()
    _engine = _build_engine(settings)
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )

    # Pool is initialized lazily — connections open on first request.
    # Avoids blocking startup with a round-trip to a remote cloud DB.
    logger.info("database.pool_ready", url=_mask_url(settings.database_url))


async def close_db() -> None:
    """Dispose engine connection pool. Called on application shutdown."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("database.disconnected")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency — yields an async database session.

    The session is automatically committed on success and rolled back
    on any exception. Always use this via Depends() in route handlers.

    Usage:
        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db_session)):
            ...
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_engine() -> AsyncEngine:
    """Return the global engine (for Alembic migrations and health checks)."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


def get_session_factory() -> "async_sessionmaker[AsyncSession]":
    """Return the global session factory (for Celery workers and background tasks)."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_factory


def _mask_url(url: str) -> str:
    """Mask password in database URL for safe logging."""
    try:
        parts = url.split("@")
        if len(parts) == 2:
            scheme_creds, host = parts
            scheme, creds = scheme_creds.split("://", 1)
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{host}"
    except Exception:  # noqa: S110 - see below
        # Deliberately silent, and deliberately not logged: this function
        # exists to keep credentials out of logs, so reporting its own
        # failure risks emitting the URL it failed to redact. Any parse
        # failure falls through to the fully redacted constant.
        pass
    return "***"
