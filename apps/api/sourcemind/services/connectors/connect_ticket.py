"""Redis state and authorization tickets for the GitHub connect flow (contract C11).

Two short-lived records, both opaque random ids:

* **state** (TTL 10 min): created by ``authorize``, bound to (workspace, user), signed with
  an HMAC keyed from the GitHub App client secret, and consumed exactly once by the callback.
* **ticket** (TTL 15 min): created by the callback after the user-token calls. It holds the
  workspace, the SourceMind user, the GitHub numeric user id, the installation id and the
  repository list the user can reach through that installation. It never holds a GitHub
  token. Creating a connector claims (deletes) it atomically, so it cannot be replayed.

A ticket only establishes the connect-time selection; it never grants continued access.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from sourcemind.core.config import get_settings
from sourcemind.core.exceptions import RateLimitExceededError, ServiceUnavailableError
from sourcemind.core.rate_limit import _INCREMENT_WITH_TTL
from sourcemind.core.redis_client import get_redis

log = structlog.get_logger(__name__)

STATE_TTL_SECONDS = 600
TICKET_TTL_SECONDS = 900
_STATE_PREFIX = "gh-connect-state:"
_TICKET_PREFIX = "gh-connect-ticket:"

# Per user and workspace. Kept here (not in core/config.py or core/rate_limit.py) so the
# connect flow does not share, and cannot exhaust, the manual-sync budget.
CONNECT_RATE_LIMITS: dict[str, tuple[int, int]] = {
    "connector-authorize": (20, 3600),
    "connector-callback": (20, 3600),
    "connector-create": (20, 3600),
    "connector-retry": (10, 3600),
    "connector-rescan": (5, 3600),
    "connector-rearm": (10, 3600),
}


class ConnectTicketError(Exception):
    """A state or ticket failed validation. ``code`` is a stable string."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ConnectTicket:
    ticket_id: str
    workspace_id: UUID
    user_id: UUID
    github_user_id: int
    installation_id: int
    repositories: tuple[str, ...]

    def canonical_repo(self, repo: str) -> str | None:
        """Ticket spelling of ``repo`` (GitHub names are case-insensitive), or None."""
        wanted = repo.casefold()
        for known in self.repositories:
            if known.casefold() == wanted:
                return known
        return None


# ── rate limiting ─────────────────────────────────────────────────────────────

async def enforce_connector_rate_limit(
    operation: str, user_id: UUID, workspace_id: UUID | None
) -> None:
    """Same atomic fail-closed counter as ``core.rate_limit`` with its own operations."""
    limit, window_seconds = CONNECT_RATE_LIMITS[operation]
    key = f"rate-limit:{operation}:{workspace_id or 'global'}:{user_id}"
    try:
        current_raw, remaining_ms_raw = await get_redis().eval(
            _INCREMENT_WITH_TTL, 1, key, window_seconds
        )
        current = int(current_raw)
        remaining_ms = int(remaining_ms_raw)
    except Exception as exc:
        log.error("rate_limit.backend_unavailable", operation=operation, error=type(exc).__name__)
        if get_settings().rate_limit_fail_closed:
            raise ServiceUnavailableError(
                "Request protection is temporarily unavailable."
            ) from exc
        return
    if current > limit:
        raise RateLimitExceededError(
            f"Rate limit exceeded for {operation}. Try again later.",
            retry_after_seconds=max(1, math.ceil(remaining_ms / 1000)),
        )


# ── state ─────────────────────────────────────────────────────────────────────

def _signing_key(client_secret: str) -> bytes:
    return hashlib.sha256(b"sourcemind-github-connect-state\x00" + client_secret.encode()).digest()


def _sign(client_secret: str, workspace_id: UUID, user_id: UUID, nonce: str) -> str:
    message = f"{workspace_id}|{user_id}|{nonce}".encode()
    return hmac.new(_signing_key(client_secret), message, hashlib.sha256).hexdigest()


async def issue_state(
    *, client_secret: str, workspace_id: UUID, user_id: UUID, redis: Any = None
) -> str:
    """Create a signed single-use state bound to (workspace, user)."""
    redis = redis or get_redis()
    nonce = secrets.token_urlsafe(32)
    await redis.set(
        _STATE_PREFIX + nonce,
        json.dumps({"w": str(workspace_id), "u": str(user_id)}),
        ex=STATE_TTL_SECONDS,
    )
    return f"{nonce}.{_sign(client_secret, workspace_id, user_id, nonce)}"


async def consume_state(
    *, client_secret: str, state: str, user_id: UUID, redis: Any = None
) -> UUID:
    """Validate and burn ``state`` for ``user_id``; return the workspace it was issued for.

    Rejects a bad signature, an unknown or expired nonce, another user's state and a replay
    with the same error code, so a caller learns nothing about which check failed.
    """
    redis = redis or get_redis()
    nonce, _, signature = state.partition(".")
    if not nonce or not signature:
        raise ConnectTicketError("connect_state_invalid")
    raw = await redis.get(_STATE_PREFIX + nonce)
    if raw is None:
        raise ConnectTicketError("connect_state_invalid")
    try:
        record = json.loads(raw)
        workspace_id = UUID(record["w"])
        issued_to = UUID(record["u"])
    except (ValueError, KeyError, TypeError):
        raise ConnectTicketError("connect_state_invalid") from None
    expected = _sign(client_secret, workspace_id, issued_to, nonce)
    if not hmac.compare_digest(expected, signature) or issued_to != user_id:
        raise ConnectTicketError("connect_state_invalid")
    # Atomic claim: of two concurrent callbacks with the same state only one wins.
    if await redis.delete(_STATE_PREFIX + nonce) != 1:
        raise ConnectTicketError("connect_state_invalid")
    return workspace_id


# ── ticket ────────────────────────────────────────────────────────────────────

async def store_ticket(
    *,
    workspace_id: UUID,
    user_id: UUID,
    github_user_id: int,
    installation_id: int,
    repositories: list[str],
    redis: Any = None,
) -> str:
    """Persist the authorization ticket (no GitHub token in it) and return its id."""
    redis = redis or get_redis()
    ticket_id = secrets.token_urlsafe(32)
    await redis.set(
        _TICKET_PREFIX + ticket_id,
        json.dumps(
            {
                "w": str(workspace_id),
                "u": str(user_id),
                "g": github_user_id,
                "i": installation_id,
                "r": repositories,
            }
        ),
        ex=TICKET_TTL_SECONDS,
    )
    return ticket_id


async def load_ticket(
    ticket_id: str, *, workspace_id: UUID, user_id: UUID, redis: Any = None
) -> ConnectTicket:
    """Return the ticket if it exists for exactly this workspace and user, else raise."""
    redis = redis or get_redis()
    raw = await redis.get(_TICKET_PREFIX + ticket_id)
    if raw is None:
        raise ConnectTicketError("connect_ticket_invalid")
    try:
        record = json.loads(raw)
        ticket = ConnectTicket(
            ticket_id=ticket_id,
            workspace_id=UUID(record["w"]),
            user_id=UUID(record["u"]),
            github_user_id=int(record["g"]),
            installation_id=int(record["i"]),
            repositories=tuple(str(repo) for repo in record["r"]),
        )
    except (ValueError, KeyError, TypeError):
        raise ConnectTicketError("connect_ticket_invalid") from None
    if ticket.workspace_id != workspace_id or ticket.user_id != user_id:
        raise ConnectTicketError("connect_ticket_invalid")
    return ticket


async def claim_ticket(ticket_id: str, *, redis: Any = None) -> bool:
    """Burn the ticket; True only for the single caller that deleted it."""
    redis = redis or get_redis()
    return await redis.delete(_TICKET_PREFIX + ticket_id) == 1
