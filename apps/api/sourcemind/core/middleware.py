"""
FastAPI middleware stack.

Middleware runs in reverse registration order (last registered = outermost).
Registration order in main.py: correlation_id → logging → timing → security headers.

CorrelationIDMiddleware:
  - Reads X-Request-ID header or generates a new UUID v4
  - Injects into structlog context so all logs within a request carry it
  - Returns X-Request-ID in response headers

RequestLoggingMiddleware:
  - Logs request start (method, path, client IP)
  - Logs request end (status code, latency in ms)
  - Skips /health endpoint to reduce noise

TimingMiddleware:
  - Adds X-Process-Time-Ms header to every response

SecurityHeadersMiddleware:
  - Adds standard security headers (CSP, HSTS, X-Content-Type-Options, etc.)
"""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger(__name__)

# Paths excluded from access logging
_SILENT_PATHS = frozenset({"/health", "/metrics", "/favicon.ico"})


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Attach a correlation ID to every request.

    Reads X-Request-ID from incoming headers (for distributed tracing),
    or generates a new UUID4. Injects into structlog context variables
    so all log records within the request lifecycle carry the request_id.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Bind to structlog context — cleared automatically per-request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        # Make request_id accessible to route handlers via request.state
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Structured access logging for every HTTP request.

    Skips health/metrics endpoints to prevent log flooding.
    Logs at INFO level for 2xx/3xx, WARNING for 4xx, ERROR for 5xx.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in _SILENT_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        client_ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )

        logger.info(
            "request.started",
            client_ip=client_ip,
            user_agent=request.headers.get("User-Agent", ""),
        )

        response = await call_next(request)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)

        log_method = (
            logger.error
            if response.status_code >= 500
            else logger.warning
            if response.status_code >= 400
            else logger.info
        )
        log_method(
            "request.completed",
            status_code=response.status_code,
            latency_ms=latency_ms,
        )

        return response


class TimingMiddleware(BaseHTTPMiddleware):
    """Add X-Process-Time-Ms header to every response."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Attach security-oriented HTTP headers to every response.

    Not a replacement for Kubernetes/Kong level security — defense in depth.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response
