"""
Neo4j connection management for the graph projection.

The graph is a read-optimised projection of PostgreSQL, fed by the outbox relay
and projector (docs/architecture/GRAPH_PROJECTION.md). PostgreSQL stays the
source of truth; every read falls back to it when the graph is off, down or
behind.

Neo4j Community has no row-level security, so tenant isolation is enforced here:
``run_read`` and ``run_write`` refuse a query that does not take a
``$workspace_id`` parameter and always bind it from the caller's authenticated
context. Callers pass parametrised Cypher only; nothing in this module builds a
query from user input.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import structlog
from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession

from sourcemind.core.config import get_settings

logger = structlog.get_logger(__name__)

_driver: AsyncDriver | None = None

# Idempotent. Community supports single-property uniqueness only, so node keys are
# synthetic ("<workspace_id>:<id>"), which also puts the tenant in the key.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT workspace_key IF NOT EXISTS FOR (w:Workspace) REQUIRE w.key IS UNIQUE",
    "CREATE CONSTRAINT user_key IF NOT EXISTS FOR (u:User) REQUIRE u.key IS UNIQUE",
    "CREATE CONSTRAINT memory_key IF NOT EXISTS FOR (m:Memory) REQUIRE m.key IS UNIQUE",
    "CREATE INDEX memory_workspace IF NOT EXISTS FOR (m:Memory) ON (m.workspace_id)",
    "CREATE INDEX user_workspace IF NOT EXISTS FOR (u:User) ON (u.workspace_id)",
)


class GraphQueryError(RuntimeError):
    """A graph query was refused before it reached Neo4j."""


async def init_graph() -> None:
    """Initialize the Neo4j async driver. Called at application startup."""
    global _driver
    settings = get_settings()

    _driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        max_connection_pool_size=20,
        connection_timeout=settings.neo4j_query_timeout_seconds,
    )

    try:
        await _driver.verify_connectivity()
        logger.info("neo4j.connected", uri=settings.neo4j_uri)
    except Exception as exc:
        logger.error("neo4j.connection_failed", error=str(exc))
        await _driver.close()
        _driver = None
        raise


async def close_graph() -> None:
    """Close the Neo4j driver. Called at application shutdown."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
        logger.info("neo4j.disconnected")


def graph_available() -> bool:
    """True when a driver exists. Says nothing about whether the server is reachable."""
    return _driver is not None


def get_graph_driver() -> AsyncDriver:
    """Return the global Neo4j driver."""
    if _driver is None:
        raise RuntimeError("Neo4j not initialized. Call init_graph() first.")
    return _driver


def get_graph_session() -> AsyncSession:
    """Return an async Neo4j session. Prefer ``run_read`` / ``run_write``.

    Usage:
        async with get_graph_session() as session:
            ...
    """
    return get_graph_driver().session()


async def ensure_schema() -> None:
    """Create constraints and indexes. Safe to call on every projector start."""
    async with get_graph_session() as session:
        for statement in SCHEMA_STATEMENTS:
            await session.run(statement)


def _checked(query: str, params: Mapping[str, Any], workspace_id: str | UUID) -> dict[str, Any]:
    if "$workspace_id" not in query:
        raise GraphQueryError("graph queries must filter on $workspace_id")
    if "workspace_id" in params and str(params["workspace_id"]) != str(workspace_id):
        raise GraphQueryError("workspace_id parameter does not match the authenticated workspace")
    return {**params, "workspace_id": str(workspace_id)}


async def run_read(
    query: str, workspace_id: str | UUID, params: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run a read query scoped to one workspace, bounded by the configured timeout."""
    bound = _checked(query, params or {}, workspace_id)
    timeout = get_settings().neo4j_query_timeout_seconds
    async with get_graph_session() as session:
        result = await session.run(query, bound, timeout=timeout)
        return [record.data() async for record in result]


async def run_write(
    query: str, workspace_id: str | UUID, params: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run a write query scoped to one workspace. Used by the projector."""
    bound = _checked(query, params or {}, workspace_id)
    async with get_graph_session() as session:
        result = await session.run(query, bound)
        return [record.data() async for record in result]
