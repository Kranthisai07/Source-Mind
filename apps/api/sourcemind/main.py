"""
SourceMind FastAPI application entry point.

Configures:
  - Lifespan context (startup/shutdown hooks for DB, Redis, Neo4j, Kafka)
  - Middleware stack (correlation ID, logging, timing, security headers)
  - Global exception handlers (SourceMindError → SM-coded JSON envelope)
  - API router inclusion (/v1/...)
  - OpenAPI metadata

Run with: uvicorn sourcemind.main:app --host 0.0.0.0 --port 8000 --reload
"""

import traceback
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import orjson
import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sourcemind.core.config import get_settings
from sourcemind.core.database import close_db, init_db
from sourcemind.core.exceptions import SourceMindError
from sourcemind.core.logging import configure_logging
from sourcemind.core.middleware import (
    CorrelationIDMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    TimingMiddleware,
)
from sourcemind.core.redis_client import close_redis, init_redis

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Startup: initialize all connections (DB, Redis, Neo4j, Kafka).
    Shutdown: gracefully close all connections and drain task queues.
    """
    settings = get_settings()
    configure_logging()

    logger.info(
        "application.starting",
        environment=settings.environment,
        version=settings.app_version,
    )

    # ── Startup ──────────────────────────────────────────────────
    try:
        await init_db()
        await init_redis()

        if settings.ff_neo4j_attribution:
            await _init_neo4j()

        if settings.ff_kafka_events:
            await _init_kafka()

    except Exception as exc:
        logger.critical("application.startup_failed", error=str(exc), exc_info=True)
        raise

    logger.info("application.started")

    yield  # ← Application serves requests here

    # ── Shutdown ─────────────────────────────────────────────────
    logger.info("application.shutting_down")

    if settings.ff_kafka_events:
        await _close_kafka()

    if settings.ff_neo4j_attribution:
        await _close_neo4j()

    await close_redis()
    await close_db()

    logger.info("application.shutdown_complete")


async def _init_neo4j() -> None:
    """Initialize Neo4j driver (deferred import to allow startup without Neo4j)."""
    try:
        from sourcemind.core.graph import init_graph
        await init_graph()
    except Exception as exc:
        logger.warning("neo4j.init_skipped", error=str(exc))


async def _close_neo4j() -> None:
    """Close Neo4j driver."""
    try:
        from sourcemind.core.graph import close_graph
        await close_graph()
    except Exception as exc:
        logger.warning("neo4j.close_error", error=str(exc))


async def _init_kafka() -> None:
    """Initialize Kafka producer."""
    try:
        from sourcemind.events.producer import init_producer
        await init_producer()
    except Exception as exc:
        logger.warning("kafka.init_skipped", error=str(exc))


async def _close_kafka() -> None:
    """Close Kafka producer."""
    try:
        from sourcemind.events.producer import close_producer
        await close_producer()
    except Exception as exc:
        logger.warning("kafka.close_error", error=str(exc))


# ─── Application factory ──────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """
    FastAPI application factory.

    Separated from module-level instantiation to allow clean test fixtures.
    """
    settings = get_settings()

    app = FastAPI(
        title="SourceMind API",
        description=(
            "Collaborative team memory platform — track what your team knows, "
            "who contributed it, how it evolved, and why decisions were made."
        ),
        version=settings.app_version,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
        default_response_class=_ORJSONResponse,
    )

    # ── Middleware (registered in reverse execution order) ────────
    # Outermost → innermost:
    #   SecurityHeaders → Timing → RequestLogging → CorrelationID → CORS → Route

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TimingMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
    )

    # ── Exception handlers ────────────────────────────────────────
    app.add_exception_handler(SourceMindError, _sourcemind_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    # ── Routes ────────────────────────────────────────────────────
    from sourcemind.api.router import api_router
    app.include_router(api_router)

    return app


# ─── Response class ───────────────────────────────────────────────────────────

class _ORJSONResponse(JSONResponse):
    """Use orjson for fast, correct JSON serialization (handles UUIDs, datetimes)."""

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(
            content,
            option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_UUID,
        )


# ─── Exception handlers ───────────────────────────────────────────────────────

async def _sourcemind_exception_handler(
    request: Request, exc: SourceMindError
) -> JSONResponse:
    """
    Translate domain exceptions into the standard SM-coded error envelope.

    Response shape:
      { error: { code, message, details, request_id } }
    """
    request_id = getattr(request.state, "request_id", "unknown")

    logger.warning(
        "request.domain_error",
        error_code=exc.code,
        error_message=exc.message,
        http_status=exc.http_status,
    )

    return _ORJSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": request_id,
            }
        },
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Translate Pydantic validation errors into SM010 error envelope."""
    request_id = getattr(request.state, "request_id", "unknown")

    errors = [
        {
            "field": ".".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]

    return _ORJSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "SM010",
                "message": "Request validation failed",
                "details": errors,
                "request_id": request_id,
            }
        },
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """
    Catch-all for unexpected exceptions.

    Logs the full traceback internally but returns a sanitized SM090 response.
    Never expose stack traces to API consumers in production.
    """
    settings = get_settings()
    request_id = getattr(request.state, "request_id", "unknown")

    logger.error(
        "request.unhandled_exception",
        error_type=type(exc).__name__,
        error=str(exc),
        exc_info=True,
    )

    # In development, include the traceback in the response for faster debugging
    details: Any = None
    if settings.is_development:
        details = traceback.format_exc()

    return _ORJSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "SM090",
                "message": "An unexpected internal error occurred.",
                "details": details,
                "request_id": request_id,
            }
        },
    )


# ─── Module-level app instance ────────────────────────────────────────────────

app = create_app()
