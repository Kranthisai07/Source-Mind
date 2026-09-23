"""Atomic Redis-backed limits for expensive authenticated operations."""

from __future__ import annotations

import math
from enum import StrEnum
from uuid import UUID

import structlog

from sourcemind.core.config import get_settings
from sourcemind.core.exceptions import RateLimitExceededError, ServiceUnavailableError
from sourcemind.core.redis_client import get_redis

log = structlog.get_logger(__name__)

_INCREMENT_WITH_TTL = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local remaining_ms = redis.call('PTTL', KEYS[1])
if remaining_ms < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1] * 1000)
  remaining_ms = ARGV[1] * 1000
end
return {current, remaining_ms}
"""


class RateLimitedOperation(StrEnum):
    SEARCH = "search"
    INGEST = "ingest"
    ANALYTICS = "analytics"
    WORKSPACE_CREATE = "workspace-create"
    CONNECTOR_SYNC = "connector-sync"
    SLACK = "slack"


def _operation_limit(operation: RateLimitedOperation) -> tuple[int, int]:
    settings = get_settings()
    limits = {
        RateLimitedOperation.SEARCH: (settings.rate_limit_search_per_minute, 60),
        RateLimitedOperation.INGEST: (settings.rate_limit_ingestion_per_minute, 60),
        RateLimitedOperation.ANALYTICS: (settings.rate_limit_analytics_per_minute, 60),
        RateLimitedOperation.WORKSPACE_CREATE: (
            settings.rate_limit_workspace_create_per_hour,
            3600,
        ),
        RateLimitedOperation.CONNECTOR_SYNC: (
            settings.rate_limit_connector_sync_per_hour,
            3600,
        ),
        RateLimitedOperation.SLACK: (settings.rate_limit_slack_per_minute, 60),
    }
    return limits[operation]


async def enforce_rate_limit(
    operation: RateLimitedOperation,
    user_id: UUID,
    workspace_id: UUID | None = None,
) -> None:
    """Increment one scoped counter atomically and reject over-limit calls."""
    limit, window_seconds = _operation_limit(operation)
    scope = str(workspace_id) if workspace_id else "global"
    key = f"rate-limit:{operation.value}:{scope}:{user_id}"
    try:
        current_raw, remaining_ms_raw = await get_redis().eval(
            _INCREMENT_WITH_TTL,
            1,
            key,
            window_seconds,
        )
        current = int(current_raw)
        remaining_ms = int(remaining_ms_raw)
    except Exception as exc:
        log.error(
            "rate_limit.backend_unavailable",
            operation=operation.value,
            error=type(exc).__name__,
        )
        if get_settings().rate_limit_fail_closed:
            raise ServiceUnavailableError(
                "Request protection is temporarily unavailable."
            ) from exc
        return

    if current > limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded for {operation.value}. Try again later.",
            retry_after_seconds=max(1, math.ceil(remaining_ms / 1000)),
        )
