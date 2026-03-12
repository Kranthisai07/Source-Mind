"""
Neo4j graph database connection management.

Used for attribution chains and relationship traversal.
The async driver is initialized once at startup and shared across requests.
"""

from typing import Any

import structlog
from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from sourcemind.core.config import get_settings

logger = structlog.get_logger(__name__)

_driver: AsyncDriver | None = None


async def init_graph() -> None:
    """Initialize Neo4j async driver. Called at application startup."""
    global _driver
    settings = get_settings()

    _driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        max_connection_pool_size=20,
        connection_timeout=10,
    )

    # Verify connectivity
    try:
        await _driver.verify_connectivity()
        logger.info("neo4j.connected", uri=settings.neo4j_uri)
    except Exception as exc:
        logger.error("neo4j.connection_failed", error=str(exc))
        raise


async def close_graph() -> None:
    """Close Neo4j driver. Called at application shutdown."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
        logger.info("neo4j.disconnected")


def get_graph_driver() -> AsyncDriver:
    """Return the global Neo4j driver."""
    if _driver is None:
        raise RuntimeError("Neo4j not initialized. Call init_graph() first.")
    return _driver


async def get_graph_session() -> AsyncSession:
    """
    Return an async Neo4j session.

    Usage (in services, not routes):
        async with await get_graph_session() as session:
            result = await session.run(...)
    """
    return get_graph_driver().session()
