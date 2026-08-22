"""
Redis connection pool management.

Uses redis-py async client with hiredis parser for performance.
Provides typed helpers for common caching patterns used across services.
"""

from collections.abc import AsyncGenerator
from typing import Any

import orjson
import structlog
from redis.asyncio import ConnectionPool, Redis

from sourcemind.core.config import get_settings

logger = structlog.get_logger(__name__)

_pool: ConnectionPool | None = None
_client: Redis | None = None  # type: ignore[type-arg]


async def init_redis() -> None:
    """
    Initialize Redis connection pool.
    Called once at application startup via lifespan.
    """
    global _pool, _client

    settings = get_settings()

    _pool = ConnectionPool.from_url(
        settings.redis_url,
        max_connections=settings.redis_pool_size,
        decode_responses=True,
        encoding="utf-8",
        health_check_interval=30,
    )
    _client = Redis(connection_pool=_pool)

    # Pool is initialized lazily — connection opens on first command.
    # Avoids blocking startup with a TLS round-trip to a remote Redis host.
    logger.info("redis.pool_ready", url=_mask_url(settings.redis_url))


async def close_redis() -> None:
    """Close Redis connection pool. Called on application shutdown."""
    global _pool, _client

    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None
    logger.info("redis.disconnected")


def get_redis() -> Redis:  # type: ignore[type-arg]
    """Return the global Redis client (not a dependency — use get_redis_dep for FastAPI)."""
    if _client is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _client


async def get_redis_dep() -> AsyncGenerator[Redis, None]:  # type: ignore[type-arg]
    """
    FastAPI dependency — yields the shared Redis client.

    The same client instance is shared across requests (pool handles concurrency).
    """
    yield get_redis()


# ─── Typed cache helpers ──────────────────────────────────────────────────────

async def cache_get(key: str) -> Any | None:
    """Get a JSON-deserialized value from cache. Returns None on miss."""
    client = get_redis()
    raw = await client.get(key)
    if raw is None:
        return None
    return orjson.loads(raw)


async def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    """Set a JSON-serialized value in cache with optional TTL (seconds)."""
    client = get_redis()
    serialized = orjson.dumps(value)
    if ttl is not None:
        await client.setex(key, ttl, serialized.decode())
    else:
        await client.set(key, serialized.decode())


async def cache_delete(key: str) -> None:
    """Delete a key from cache."""
    client = get_redis()
    await client.delete(key)


async def cache_exists(key: str) -> bool:
    """Check if a key exists in cache."""
    client = get_redis()
    return bool(await client.exists(key))


def embedding_cache_key(content_hash: str) -> str:
    """Canonical cache key for embedding vectors."""
    return f"embedding:v1:{content_hash}"


def session_cache_key(user_id: str) -> str:
    """Canonical cache key for user sessions."""
    return f"session:v1:{user_id}"


def rate_limit_key(identifier: str, window: str) -> str:
    """Canonical cache key for rate limiting."""
    return f"ratelimit:v1:{identifier}:{window}"


def _mask_url(url: str) -> str:
    """Mask password in Redis URL for safe logging."""
    try:
        if "@" in url:
            parts = url.rsplit("@", 1)
            scheme_creds = parts[0]
            host = parts[1]
            if ":" in scheme_creds.split("://", 1)[-1]:
                scheme, creds = scheme_creds.split("://", 1)
                user_pass = creds.rsplit(":", 1)
                return f"{scheme}://{user_pass[0]}:***@{host}"
    except Exception:  # noqa: S110 - see below
        # Silent by design: this is the log-redaction helper itself, so
        # logging the failure could leak the credentials being redacted.
        pass
    return url
