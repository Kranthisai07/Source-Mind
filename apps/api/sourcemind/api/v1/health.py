"""
Health check endpoint.

GET /health — returns component status for all critical dependencies.

Used by:
  - Kubernetes readiness/liveness probes
  - docker-compose healthcheck
  - Load balancer health checks

Response format intentionally does NOT use the standard API envelope —
health checks are consumed by infrastructure, not application clients.
"""

import time
from typing import Any, Literal

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from sourcemind.core.database import get_engine
from sourcemind.core.redis_client import get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])

ComponentStatus = Literal["healthy", "degraded", "unhealthy"]


async def _check_postgres() -> dict[str, Any]:
    """Verify PostgreSQL connectivity and pgvector extension."""
    start = time.perf_counter()
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1 AS ok, version() AS version"))
            row = result.mappings().first()
            # Verify pgvector is installed
            await conn.execute(text("SELECT '[1,2,3]'::vector(3)"))
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "healthy",
            "latency_ms": latency_ms,
            "version": str(row["version"])[:80] if row else "unknown",
            "pgvector": "enabled",
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error("health.postgres_check_failed", error=str(exc))
        return {
            "status": "unhealthy",
            "latency_ms": latency_ms,
            "error": str(exc),
        }


async def _check_redis() -> dict[str, Any]:
    """Verify Redis connectivity and measure round-trip latency."""
    start = time.perf_counter()
    try:
        redis = get_redis()
        pong = await redis.ping()
        info = await redis.info("server")
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "healthy" if pong else "unhealthy",
            "latency_ms": latency_ms,
            "version": info.get("redis_version", "unknown"),
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.error("health.redis_check_failed", error=str(exc))
        return {
            "status": "unhealthy",
            "latency_ms": latency_ms,
            "error": str(exc),
        }


async def _check_neo4j() -> dict[str, Any]:
    """Verify Neo4j connectivity."""
    start = time.perf_counter()
    try:
        from sourcemind.core.graph import get_graph_driver
        driver = get_graph_driver()
        await driver.verify_connectivity()
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "status": "unhealthy",
            "latency_ms": latency_ms,
            "error": str(exc),
        }


@router.get("/health", summary="Component health check")
async def health_check() -> JSONResponse:
    """
    Check the health of all critical infrastructure components.

    Returns 200 if all components are healthy.
    Returns 503 if any critical component is unhealthy.

    Critical components: PostgreSQL, Redis
    Non-critical (degraded but not failing): Neo4j, Kafka
    """
    from sourcemind.core.config import get_settings
    settings = get_settings()

    start = time.perf_counter()

    # Run all checks (non-critical ones may fail without affecting overall status)
    postgres_status = await _check_postgres()
    redis_status = await _check_redis()

    components: dict[str, Any] = {
        "postgresql": postgres_status,
        "redis": redis_status,
    }

    # Neo4j check (non-critical in dev if FF disabled)
    if settings.ff_neo4j_attribution:
        components["neo4j"] = await _check_neo4j()

    # Determine overall status
    critical_healthy = (
        postgres_status["status"] == "healthy"
        and redis_status["status"] == "healthy"
    )
    any_unhealthy = any(
        c.get("status") == "unhealthy" for c in components.values()
    )

    if critical_healthy and not any_unhealthy:
        overall: ComponentStatus = "healthy"
    elif critical_healthy:
        overall = "degraded"
    else:
        overall = "unhealthy"

    total_ms = round((time.perf_counter() - start) * 1000, 2)
    http_status = 200 if overall in ("healthy", "degraded") else 503

    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall,
            "version": settings.app_version,
            "environment": settings.environment,
            "components": components,
            "total_latency_ms": total_ms,
        },
    )
