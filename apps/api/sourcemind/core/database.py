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
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool

from sourcemind.core.config import Environment, get_settings

logger = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine(settings: Any = None) -> AsyncEngine:
    """
    Construct the async SQLAlchemy engine.

    Uses QueuePool for production, NullPool for tests to avoid
    connection leaks between test cases.
    """
    if settings is None:
        settings = get_settings()

    pool_class = (
        NullPool
        if settings.environment == Environment.DEVELOPMENT and settings.debug
        else QueuePool
    )

    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_recycle=settings.database_pool_recycle,
        pool_pre_ping=True,
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

    # Verify connection is reachable
    try:
        async with _engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            row = result.scalar()
            logger.info("database.connected", result=row, url=_mask_url(settings.database_url))
    except Exception as exc:
        logger.error("database.connection_failed", error=str(exc))
        raise


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
    except Exception:
        pass
    return "***"
