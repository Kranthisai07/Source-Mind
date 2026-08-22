"""
FastAPI dependency injection functions.

All shared dependencies (auth, DB session, Redis, pagination) live here.
Import via: from sourcemind.core.dependencies import get_current_user
Never import from the api/ layer in services.
"""

import base64
import uuid
from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import Depends, Header, Query, Request
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.database import get_db_session
from sourcemind.core.exceptions import (
    InternalError,
    InvalidIdempotencyKeyError,
    UnauthorizedError,
    WorkspaceAccessDeniedError,
)
from sourcemind.core.redis_client import get_redis_dep

# In-memory JWKS cache: {url: {"keys": [...], "fetched_at": float}}
_jwks_cache: dict[str, dict] = {}

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


def _clerk_jwks_url(publishable_key: str) -> str:
    """
    Derive the JWKS URL from a Clerk publishable key.

    Publishable key format: pk_test_<base64(frontend_api_host + '$')>
    The frontend API host is the Clerk instance URL.

    Raises:
        InternalError: if the key is missing or unparseable.

    There is deliberately no fallback. The previous fallback returned
    https://api.clerk.com/v1/jwks, which is an authenticated endpoint: the
    JWKS fetch sends no credentials, so it always answers 401 and every token
    verification fails. In production that surfaced only as a confusing
    "Token verification error: 401 for url .../jwks" on every request, hours
    after the real mistake — an unset CLERK_PUBLISHABLE_KEY. A fallback that
    can never succeed is worse than no fallback, so this fails loudly and
    names the variable to fix.
    """
    if not publishable_key:
        raise InternalError(
            "CLERK_PUBLISHABLE_KEY is not set. It is required to derive the "
            "Clerk JWKS URL used to verify tokens; without it no request can "
            "authenticate."
        )
    try:
        parts = publishable_key.split("_", 2)
        if len(parts) < 3 or not parts[2]:
            raise ValueError(f"malformed publishable key: {publishable_key[:12]!r}")
        b64 = parts[2]
        # Pad base64 to multiple of 4
        padding = 4 - len(b64) % 4
        if padding != 4:
            b64 += "=" * padding
        host = base64.b64decode(b64).decode().rstrip("$")
        if not host:
            raise ValueError("publishable key decoded to an empty host")
        return f"https://{host}/.well-known/jwks.json"
    except InternalError:
        raise
    except Exception as exc:
        raise InternalError(
            f"CLERK_PUBLISHABLE_KEY could not be parsed into a JWKS URL: {exc}"
        ) from exc


async def _fetch_jwks(jwks_url: str) -> list[dict]:
    """Fetch and cache JWKS keys with a 5-minute TTL."""
    import time

    cached = _jwks_cache.get(jwks_url)
    if cached and (time.time() - cached["fetched_at"]) < 300:
        return cached["keys"]

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        data = resp.json()

    _jwks_cache[jwks_url] = {"keys": data["keys"], "fetched_at": time.time()}
    return data["keys"]


async def _verify_clerk_token(token: str, jwks_url: str) -> dict:
    """Verify a Clerk JWT and return its claims."""
    keys = await _fetch_jwks(jwks_url)
    # Try each key — Clerk rotates keys
    last_err: Exception | None = None
    for key in keys:
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
            return claims
        except JWTError as e:
            last_err = e
    raise UnauthorizedError(f"Token verification failed: {last_err}")


# Simple in-memory cache for Clerk user profiles {clerk_id: (email, display_name)}
_user_profile_cache: dict[str, tuple[str, str | None]] = {}


async def _fetch_clerk_user_profile(
    clerk_id: str, secret_key: str
) -> tuple[str, str | None]:
    """
    Fetch email + display name from Clerk's Users API.
    Results are cached in-process (profiles rarely change mid-session).
    Falls back to a placeholder email if the API call fails.
    """
    if clerk_id in _user_profile_cache:
        return _user_profile_cache[clerk_id]

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"https://api.clerk.com/v1/users/{clerk_id}",
                headers={"Authorization": f"Bearer {secret_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        # Primary email address
        email_id = data.get("primary_email_address_id")
        email = next(
            (e["email_address"] for e in data.get("email_addresses", []) if e["id"] == email_id),
            f"{clerk_id}@clerk.local",
        )
        first = data.get("first_name") or ""
        last = data.get("last_name") or ""
        display_name: str | None = (f"{first} {last}".strip()) or None

        _user_profile_cache[clerk_id] = (email, display_name)
        return email, display_name

    except Exception as exc:
        logger.warning("auth.clerk_profile_fetch_failed", clerk_id=clerk_id, error=str(exc))
        fallback = (f"{clerk_id}@clerk.local", None)
        _user_profile_cache[clerk_id] = fallback
        return fallback


async def _get_or_create_user(
    db: AsyncSession,
    clerk_id: str,
    email: str,
    display_name: str | None,
) -> "AuthenticatedUser":
    """
    Look up an existing user by clerk_id, or create one on first login.
    Returns an AuthenticatedUser populated from the DB row.
    """
    from sourcemind.models.user import User

    result = await db.execute(select(User).where(User.clerk_id == clerk_id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            clerk_id=clerk_id,
            email=email,
            display_name=display_name,
        )
        db.add(user)
        await db.flush()
        logger.info("auth.user_provisioned", clerk_id=clerk_id, email=email)

    return AuthenticatedUser(
        user_id=user.id,
        clerk_id=user.clerk_id,
        email=user.email,
        display_name=user.display_name,
    )


async def get_current_user(
    request: Request,
    db: DBSession,
) -> "AuthenticatedUser":
    """
    Extract and verify the current user from the Clerk JWT bearer token.

    Verifies the token signature against the Clerk instance JWKS endpoint,
    extracts claims, and resolves (or auto-provisions) the internal user record.

    In development mode with no Clerk key configured, returns a mock user.
    """
    from sourcemind.core.config import get_settings
    settings = get_settings()

    # Development bypass: no Clerk key → mock user for local testing
    if settings.is_development and not settings.clerk_secret_key:
        logger.warning("auth.bypassed", reason="CLERK_SECRET_KEY not set")
        return AuthenticatedUser(
            user_id=UUID("00000000-0000-4000-8000-000000000001"),
            clerk_id="dev_user_1",
            email="dev@sourcemind.local",
            display_name="Dev User",
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise UnauthorizedError(
            "Authorization header required. Format: 'Bearer <jwt>'"
        )

    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise UnauthorizedError("Bearer token is empty.")

    jwks_url = _clerk_jwks_url(settings.clerk_publishable_key)
    try:
        claims = await _verify_clerk_token(token, jwks_url)
    except UnauthorizedError:
        raise
    except Exception as exc:
        raise UnauthorizedError(f"Token verification error: {exc}") from exc

    clerk_id: str = claims.get("sub", "")
    if not clerk_id:
        raise UnauthorizedError("JWT missing 'sub' claim.")

    # Clerk JWT v2 doesn't embed email — fetch user profile from Clerk API.
    # Cache the result in _user_profile_cache to avoid repeated API calls.
    email, display_name = await _fetch_clerk_user_profile(clerk_id, settings.clerk_secret_key)

    return await _get_or_create_user(db, clerk_id, email, display_name)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


# ─── Request ID ───────────────────────────────────────────────────────────────

def get_request_id(request: Request) -> str:
    """Extract the correlation ID set by CorrelationIDMiddleware."""
    return getattr(request.state, "request_id", "unknown")


RequestID = Annotated[str, Depends(get_request_id)]


# ─── Workspace role authorization ─────────────────────────────────────────────

async def require_workspace_role(
    session: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    allowed_roles: set[str],
) -> str:
    """Assert the user holds one of `allowed_roles` in the workspace.

    Returns the user's role, or raises WorkspaceAccessDeniedError (SM005, 403)
    if they are not a member or hold an insufficient role.

    This is the first role gate in the codebase. No prior pattern existed to
    reuse: workspaces.py queries WorkspaceMember.role to FIND the owner rather
    than to authorize, and AuthenticatedUser.workspace_role is declared but
    never assigned, so it is always None. It is defined here, beside the other
    auth dependencies, so later gates have something to follow rather than
    each inventing its own.
    """
    row = (
        await session.execute(
            text(
                "SELECT role FROM workspace_members "
                "WHERE workspace_id = CAST(:ws AS uuid) "
                "  AND user_id = CAST(:uid AS uuid)"
            ),
            {"ws": str(workspace_id), "uid": str(user_id)},
        )
    ).first()

    if row is None:
        raise WorkspaceAccessDeniedError(
            "You are not a member of this workspace."
        )
    role = row.role
    if role not in allowed_roles:
        raise WorkspaceAccessDeniedError(
            f"This action requires one of: {', '.join(sorted(allowed_roles))}. "
            f"Your role is '{role}'."
        )
    return role
