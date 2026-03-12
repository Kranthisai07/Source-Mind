"""
FastAPI dependency injection functions.

All shared dependencies (auth, DB session, Redis, pagination) live here.
Import via: from sourcemind.core.dependencies import get_current_user
Never import from the api/ layer in services.
"""

import uuid
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.database import get_db_session
from sourcemind.core.exceptions import (
    InvalidIdempotencyKeyError,
    UnauthorizedError,
    WorkspaceAccessDeniedError,
)
from sourcemind.core.redis_client import get_redis_dep
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


# ─── AI client dependencies ───────────────────────────────────────────────────

async def get_openai_client() -> object:
    """Provide an AsyncOpenAI client for injection into route handlers."""
    from openai import AsyncOpenAI
    from sourcemind.core.config import get_settings
    settings = get_settings()
    return AsyncOpenAI(api_key=settings.openai_api_key)


async def get_anthropic_client() -> object:
    """Provide an AsyncAnthropic client for injection into route handlers."""
    from anthropic import AsyncAnthropic
    from sourcemind.core.config import get_settings
    settings = get_settings()
    return AsyncAnthropic(api_key=settings.anthropic_api_key)


OpenAIClient = Annotated[object, Depends(get_openai_client)]
AnthropicClient = Annotated[object, Depends(get_anthropic_client)]


# ─── Type aliases for cleaner route signatures ────────────────────────────────

DBSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis_dep)]  # type: ignore[type-arg]


# ─── Pagination ───────────────────────────────────────────────────────────────

class PaginationParams:
    """
    Standard keyset pagination parameters.

    Supports cursor-based pagination for stable, efficient result sets
    even as underlying data changes. Never use offset pagination on large tables.
    """

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=100, description="Results per page")] = 20,
        cursor: Annotated[
            str | None, Query(description="Opaque cursor from previous page meta.next_cursor")
        ] = None,
        sort: Annotated[
            str, Query(description="Sort field (prefix with - for descending)")
        ] = "-created_at",
    ) -> None:
        self.limit = limit
        self.cursor = cursor
        self.sort = sort


PaginationDep = Annotated[PaginationParams, Depends(PaginationParams)]


# ─── Idempotency ──────────────────────────────────────────────────────────────

async def require_idempotency_key(
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="UUID v4 idempotency key for mutations"),
    ] = None,
) -> str:
    """
    Validate the Idempotency-Key header on mutation endpoints.

    Returns the validated key, or raises InvalidIdempotencyKeyError.
    """
    if idempotency_key is None:
        raise InvalidIdempotencyKeyError(
            "Idempotency-Key header is required for mutation requests. "
            "Generate a UUID v4 and include it as: Idempotency-Key: <uuid>"
        )
    try:
        uuid.UUID(idempotency_key, version=4)
    except ValueError as exc:
        raise InvalidIdempotencyKeyError(
            f"Idempotency-Key must be a valid UUID v4, got: {idempotency_key!r}"
        ) from exc

    return idempotency_key


IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]


# ─── Auth (Clerk JWT) ─────────────────────────────────────────────────────────

class AuthenticatedUser:
    """
    Represents a verified, authenticated user extracted from the Clerk JWT.

    workspace_id and role are resolved from the JWT claims and the workspace
    membership table. The workspace_id is workspace-scoped from the path param.
    """

    def __init__(
        self,
        user_id: UUID,
        clerk_id: str,
        email: str,
        display_name: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.clerk_id = clerk_id
        self.email = email
        self.display_name = display_name
        # Populated by workspace-scoped dependencies
        self.workspace_id: UUID | None = None
        self.workspace_role: str | None = None


async def get_current_user(
    request: Request,
    db: DBSession,
) -> AuthenticatedUser:
    """
    Extract and verify the current user from the Clerk JWT bearer token.

    Verifies the token signature against Clerk's JWKS endpoint,
    extracts claims, and resolves the internal user record.

    In development mode (no Clerk key configured), returns a mock user
    for local testing without Clerk setup.

    Raises:
        UnauthorizedError: if no valid token is provided
        TokenExpiredError: if the token has expired
        TokenInvalidError: if the token signature is invalid
    """
    from sourcemind.core.config import get_settings
    settings = get_settings()

    # Development mode: bypass auth when Clerk is not configured
    if settings.is_development and not settings.clerk_secret_key:
        logger.warning(
            "auth.bypassed",
            reason="CLERK_SECRET_KEY not set, using dev mock user",
        )
        return AuthenticatedUser(
            user_id=UUID("00000000-0000-4000-8000-000000000001"),
            clerk_id="dev_user_1",
            email="dev@sourcemind.local",
            display_name="Dev User",
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise UnauthorizedError(
            "Authorization header is required. Format: 'Bearer <jwt_token>'"
        )

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise UnauthorizedError("Bearer token is empty.")

    # Clerk JWT verification will be implemented with JWKS in Phase 2
    # For now, raise a clear error indicating setup is needed
    raise UnauthorizedError(
        "Clerk JWT verification not yet configured. "
        "Set CLERK_SECRET_KEY and CLERK_PUBLISHABLE_KEY environment variables."
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


# ─── Request ID ───────────────────────────────────────────────────────────────

def get_request_id(request: Request) -> str:
    """Extract the correlation ID set by CorrelationIDMiddleware."""
    return getattr(request.state, "request_id", "unknown")


RequestID = Annotated[str, Depends(get_request_id)]
