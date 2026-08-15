"""
Structured JSON logging configuration using structlog.

Every log record includes:
  - timestamp (ISO 8601)
  - level
  - logger name
  - request_id (injected by middleware)
  - environment
  - Any additional key=value context from the call site

In development: colored console output for readability.
In production: JSON lines to stdout (parsed by Datadog).
"""

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from sourcemind.core.config import Environment, get_settings


def _add_app_context(
    logger: Any, method_name: str, event_dict: EventDict
) -> EventDict:
    """Inject application-level fields into every log record."""
    settings = get_settings()
    event_dict["app"] = "sourcemind-api"
    event_dict["version"] = settings.app_version
    event_dict["env"] = settings.environment
    return event_dict


def _drop_color_message_key(
    logger: Any, method_name: str, event_dict: EventDict
) -> EventDict:
    """Remove uvicorn's color_message key — it duplicates 'event' with ANSI codes."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging() -> None:
    """
    Configure structlog for the application.

    Call once at startup. Subsequent calls are idempotent.
    """
    settings = get_settings()

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _add_app_context,
        _drop_color_message_key,
    ]

    if settings.environment == Environment.DEVELOPMENT:
        # Pretty, colored output for local dev
        processors: list[Processor] = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # JSON lines for production log aggregation
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    # Use stdlib LoggerFactory so structlog.stdlib.add_logger_name works correctly.
    # This routes all structlog output through Python's stdlib logging handlers.
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route standard library logging through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelName(settings.log_level),
        force=True,  # Override any existing handlers
    )

    # Silence noisy third-party loggers in production
    if not settings.is_development:
        for noisy_logger in ("uvicorn.access", "sqlalchemy.engine", "httpx"):
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)
