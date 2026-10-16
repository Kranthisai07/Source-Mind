"""Workspace MCP API-key authentication for the MCP endpoint (decision D-048).

A pure ASGI middleware, deliberately NOT the SDK's OAuth plumbing: that would advertise an
OAuth authorization server in its metadata, which does not exist here. It authenticates
``Authorization: Bearer smk_...`` on every request to the MCP endpoint and binds the key's
identity (key, workspace, creating member) to a context variable the tool handlers read.

Rules:

* The key is resolved against PostgreSQL on EVERY request: no cache, so a revoked or expired
  key stops working on its next call.
* A missing, malformed, oversized, duplicated, unknown, revoked or expired credential is the same
  401; nothing tells the caller which. A database failure is a 503 (temporary), never a 401: a
  temporary fault must not look like an invalid credential.
* The key and its hash are never logged. Only the key id is.
* Membership, role and workspace-deletion checks are NOT done here: the tools run them on every
  call through the existing read path, with the creating member's identity.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import structlog

from sourcemind.services.mcp_keys import resolve_key, touch_key

log = structlog.get_logger(__name__)

# smk_ + 43 url-safe characters (secrets.token_urlsafe(32)). Anything else cannot be a key, so it
# is rejected before any database work; this also bounds the work an unauthenticated caller costs.
_KEY_PATTERN = re.compile(r"^smk_[A-Za-z0-9_-]{43}$")
_MAX_HEADER_BYTES = 128

SessionFactoryProvider = Callable[[], Callable[[], Any]]
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]
ASGIApp = Callable[[dict, Receive, Send], Awaitable[None]]


@dataclass(frozen=True)
class KeyPrincipal:
    """Identity carried by a valid key: the key, its single workspace, and its creator."""

    key_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID


current_key_principal: ContextVar[KeyPrincipal | None] = ContextVar(
    "mcp_key_principal", default=None
)


def bearer_key_from_headers(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Return a well-formed key from exactly one Authorization header, else None."""
    values = [value for name, value in headers if name.lower() == b"authorization"]
    if len(values) != 1 or len(values[0]) > _MAX_HEADER_BYTES:
        return None
    try:
        text = values[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    scheme, _, token = text.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token if _KEY_PATTERN.fullmatch(token) else None


async def authenticate_key(
    session_factory: SessionFactoryProvider, key: str
) -> KeyPrincipal | None:
    """Resolve a key to its principal, or None if unknown, revoked or expired."""
    async with session_factory()() as session:
        resolved = await resolve_key(session, key)
        if resolved is None:
            return None
        try:
            await touch_key(session, resolved.key_id, key)
            await session.commit()
        except Exception as exc:  # last_used_at is best effort and must never deny a valid key
            await session.rollback()
            log.warning(
                "mcp.key_touch_failed", key_id=str(resolved.key_id), error=type(exc).__name__
            )
        return KeyPrincipal(
            key_id=resolved.key_id, workspace_id=resolved.workspace_id, user_id=resolved.user_id
        )


async def _respond(
    send: Send,
    status: int,
    body: dict[str, str],
    extra: list[tuple[bytes, bytes]],
) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                (b"cache-control", b"no-store"),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class ApiKeyAuthMiddleware:
    """Authenticate workspace MCP keys on the MCP endpoint path; pass every other path through."""

    def __init__(self, app: ASGIApp, *, path: str, session_factory: SessionFactoryProvider) -> None:
        self._app = app
        self._path = path.rstrip("/") or "/"
        self._session_factory = session_factory

    async def __call__(self, scope: dict, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (scope.get("path", "").rstrip("/") or "/") != self._path:
            await self._app(scope, receive, send)
            return

        key = bearer_key_from_headers(scope.get("headers", []))
        if key is None:
            await self._unauthorized(send)
            return
        try:
            principal = await authenticate_key(self._session_factory, key)
        except Exception as exc:  # database or pool failure: temporary, never a credential error
            log.warning("mcp.key_lookup_unavailable", error=type(exc).__name__)
            await _respond(
                send,
                503,
                {"error": "temporarily_unavailable"},
                [(b"retry-after", b"5")],
            )
            return
        if principal is None:
            await self._unauthorized(send)
            return

        token = current_key_principal.set(principal)
        try:
            await self._app(scope, receive, send)
        finally:
            current_key_principal.reset(token)

    @staticmethod
    async def _unauthorized(send: Send) -> None:
        await _respond(
            send,
            401,
            {"error": "invalid_token"},
            [(b"www-authenticate", b'Bearer realm="sourcemind-mcp", error="invalid_token"')],
        )
