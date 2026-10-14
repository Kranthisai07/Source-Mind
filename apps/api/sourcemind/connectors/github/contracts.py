"""Interfaces between the GitHub-facing code and the sync orchestrator.

These are CONTRACTS (see docs/architecture/AUTOMATIC_INGESTION_CONTRACT.md), written
first so the packages that implement and consume them can be built in parallel:

* discovery / mapping / verification (``client.py``, ``mapper.py``, ``discovery.py``,
  ``verifier.py``) implement them;
* the sync orchestrator (``connector.py``, ``workers/connector_tasks.py``) consumes them.

Nothing here performs I/O.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

# ── Artifact kinds ───────────────────────────────────────────────────────────
#: ``source_type`` of every authored segment (C1). One segment = one ArtifactLink identity.
SEGMENT_TYPES = (
    "commit",
    "pull_request",
    "pull_review",
    "pull_review_comment",
    "issue",
    "issue_comment",
)

#: Discovery scopes: the ``artifact_type`` column of ``connector_sync_cursors`` (C2/C3).
#: Several scopes may emit the same segment types (e.g. both ``pull_requests`` and
#: ``review_reconcile`` emit ``pull_review``); a segment's identity is its source_id.
SCOPES = (
    "commits",
    "pull_requests",
    "issues",
    "issue_comments",
    "review_comments",
    "review_reconcile",
)


# ── Typed fetch errors (never a silent empty result) ─────────────────────────
class GitHubFetchError(Exception):
    """Base for errors the orchestrator must classify, not swallow."""

    code = "github_error"


class RateLimitedError(GitHubFetchError):
    """Primary or secondary rate limit. The run ends; the discovery position is kept."""

    code = "rate_limited"

    def __init__(self, retry_after_s: float | None = None, reset_at: datetime | None = None):
        super().__init__(f"rate limited (retry_after={retry_after_s}, reset_at={reset_at})")
        self.retry_after_s = retry_after_s
        self.reset_at = reset_at


class PermissionDeniedError(GitHubFetchError):
    """401/403 that is NOT a rate limit: the scope pauses (C11); nothing is retired."""

    code = "permission_denied"


class NotFoundError(GitHubFetchError):
    """404. GitHub also answers 404 for resources hidden by permissions, so this means
    *unavailable*, never *deleted* (C7)."""

    code = "not_found"


class GoneError(GitHubFetchError):
    """410. The only single-response signal that may establish deletion (C7)."""

    code = "gone"


class TransientError(GitHubFetchError):
    """5xx or network failure: retried with backoff."""

    code = "transient"


# ── Discovery state (mirrors connector_sync_cursors) ─────────────────────────
@dataclass(frozen=True)
class ScopeState:
    phase: str = "backfill"  # 'backfill' | 'incremental'
    window_low: datetime | None = None
    window_high: datetime | None = None
    resume_ts: datetime | None = None
    resume_id: str | None = None
    listing_complete: bool = False
    head_sha: str | None = None  # commits scope: default-branch head already covered


@dataclass(frozen=True)
class DiscoveryLimits:
    max_items: int = 300  # handoff budget for this scope in this run
    import_days: int = 90
    max_items_per_type: int = 500
    max_segments_per_pr: int = 30
    reconcile_batch: int = 25
    max_artifact_chars: int = 40_000
    short_segment_verbatim_max_chars: int = 200
    skip_bots: bool = True
    skip_empty_body: bool = True


@dataclass(frozen=True)
class Discovered:
    """One authored segment ready for handoff.

    ``position`` is the resume key to persist IN THE SAME TRANSACTION as this item's
    handoff (C3): the (timestamp, id) of this item in the scope's traversal order.
    """

    document: Any  # sourcemind.connectors.github.mapper.ConnectorDocument
    position: tuple[datetime | None, str]
    scope_complete_after: bool = False


@dataclass
class ScopeResult:
    """Available after ``ScopeRun.items()`` is exhausted."""

    state: ScopeState  # persist once the last yielded item is durably handed off
    inventory_complete: bool  # listing exhausted; no cap, error or rate limit hit (C7)
    filtered: dict[str, int] = field(default_factory=dict)  # reason -> count (visible)
    truncated: dict[str, int] = field(default_factory=dict)  # e.g. segments over the PR cap
    seen_source_ids: frozenset[str] = frozenset()  # for the two-scan deletion rule


class ScopeRun(Protocol):
    """A single bounded pass over one scope.

    ``items()`` yields in traversal order and stops after ``limits.max_items`` or when
    the window is exhausted. It raises a ``GitHubFetchError`` subclass on failure
    (RateLimitedError, PermissionDeniedError, NotFoundError, GoneError, TransientError)
    and never returns a partial silent success.
    """

    result: ScopeResult | None

    def items(self) -> AsyncIterator[Discovered]: ...


# ── Repository authorization check (C11) ─────────────────────────────────────
class AccessVerdict(StrEnum):
    AUTHORIZED = "authorized"
    DENIED = "denied"  # definitive: the user no longer has read access
    UNVERIFIED = "unverified"  # service unavailable / ambiguous / mechanism unestablished


@dataclass(frozen=True)
class VerifierResult:
    verdict: AccessVerdict
    reason_code: str  # stable, e.g. 'ok', 'denied', 'ambiguous_404', 'verifier_unavailable'


class RepoAccessVerifier(Protocol):
    """Current-access check run before EVERY content-fetch batch. Anything other than
    AUTHORIZED means: no content endpoint may be called for that scope."""

    async def verify(self, *, owner: str, repo: str, github_user_id: int) -> VerifierResult: ...
