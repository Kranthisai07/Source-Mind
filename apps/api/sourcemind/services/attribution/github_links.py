"""Admin-asserted GitHub author links: lookup, credit-time recheck, finalization.

D-021. A GitHub-synced artifact is credited to a SourceMind user ONLY through
an explicit ``github_author_links`` row in the same workspace, keyed by
GitHub's NUMERIC user id, whose target is still an active member with a live
user row. Logins, display names and e-mails are labels; nothing here ever
compares them. The person who triggered the sync is never credited.

Every lookup runs inside a SAVEPOINT and fails closed: a database error makes
the author unresolved and logs ``github_link_lookup_failed``. It never credits
anyone and never aborts ingestion.

``artifact_links.resolved_user_id`` is written only by
:func:`finalize_external_attribution`, in the worker transaction that writes
the creation attribution. Invariant: ``resolved_user_id IS NOT NULL`` exactly
when the creation attribution credits that user.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# Sync-time resolution values stored in pipeline_data["attribution"].
LINKED = "linked"
UNLINKED = "unlinked"
LOOKUP_FAILED = "lookup_failed"
NO_GITHUB_ACCOUNT = "no_github_account"

_RESOLVE_SQL = text(
    """
    SELECT gal.user_id
    FROM github_author_links AS gal
    JOIN workspace_members AS wm
      ON wm.workspace_id = gal.workspace_id
     AND wm.user_id = gal.user_id
    JOIN users AS u
      ON u.id = gal.user_id
    WHERE gal.workspace_id = CAST(:ws AS uuid)
      AND gal.github_user_id = :gid
      AND wm.status = 'active'
      AND wm.departed_at IS NULL
      AND u.deleted_at IS NULL
    """
)

# Locks only the link row. A concurrent admin correction or deletion waits
# until the worker commits; one committed before this lock is observed.
_CURRENT_LINK_SQL = text(
    """
    SELECT gal.user_id
    FROM github_author_links AS gal
    WHERE gal.workspace_id = CAST(:ws AS uuid)
      AND gal.github_user_id = :gid
    FOR SHARE
    """
)

_ACTIVE_MEMBER_SQL = text(
    """
    SELECT 1 AS ok
    FROM workspace_members AS wm
    JOIN users AS u
      ON u.id = wm.user_id
    WHERE wm.workspace_id = CAST(:ws AS uuid)
      AND wm.user_id = CAST(:uid AS uuid)
      AND wm.status = 'active'
      AND wm.departed_at IS NULL
      AND u.deleted_at IS NULL
    """
)


@dataclass(frozen=True)
class LinkLookup:
    """Sync-time lookup result."""

    user_id: uuid.UUID | None
    status: str


@dataclass(frozen=True)
class CreditDecision:
    """Credit-time decision for one external document."""

    status: str  # "credited" | "unresolved"
    user_id: uuid.UUID | None
    reason: str | None

    def final_record(self) -> dict[str, Any]:
        if self.status == "credited":
            return {"status": "credited", "user_id": str(self.user_id)}
        return {"status": "unresolved", "reason": self.reason}


def valid_github_user_id(value: object) -> int | None:
    """Return a positive integer id, or None. Strings and bools are rejected."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _as_uuid(value: object) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def resolve_github_link(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    github_user_id: object,
) -> LinkLookup:
    """Sync-time lookup of the admin-asserted link for a numeric GitHub id."""
    gid = valid_github_user_id(github_user_id)
    if gid is None:
        return LinkLookup(None, NO_GITHUB_ACCOUNT)
    try:
        async with session.begin_nested():
            row = (
                await session.execute(_RESOLVE_SQL, {"ws": str(workspace_id), "gid": gid})
            ).first()
    except Exception as exc:
        log.error(
            "github_link_lookup_failed",
            stage="sync",
            workspace_id=str(workspace_id),
            error_type=type(exc).__name__,
        )
        return LinkLookup(None, LOOKUP_FAILED)
    if row is None:
        return LinkLookup(None, UNLINKED)
    return LinkLookup(_as_uuid(row.user_id), LINKED)


async def recheck_external_credit(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    attribution: dict[str, Any],
) -> CreditDecision:
    """Decide at credit time whether the sync-time link may still be honoured.

    Credits only if the CURRENT link still maps the numeric id to the SAME user
    recorded at sync time and that user is still an active member. A corrected
    link is not followed; it applies to future syncs only.
    """
    gid = valid_github_user_id(attribution.get("github_user_id"))
    if gid is None:
        return CreditDecision("unresolved", None, NO_GITHUB_ACCOUNT)
    recorded = attribution.get("link_user_id")
    if not recorded:
        return CreditDecision("unresolved", None, "unlinked_at_sync")
    try:
        recorded_user = _as_uuid(recorded)
    except ValueError:
        return CreditDecision("unresolved", None, "unlinked_at_sync")

    try:
        async with session.begin_nested():
            current = (
                await session.execute(
                    _CURRENT_LINK_SQL, {"ws": str(workspace_id), "gid": gid}
                )
            ).first()
            if current is None:
                return CreditDecision("unresolved", None, "link_removed")
            if _as_uuid(current.user_id) != recorded_user:
                return CreditDecision("unresolved", None, "link_changed")
            member = (
                await session.execute(
                    _ACTIVE_MEMBER_SQL,
                    {"ws": str(workspace_id), "uid": str(recorded_user)},
                )
            ).first()
            if member is None:
                return CreditDecision("unresolved", None, "member_inactive")
    except Exception as exc:
        log.error(
            "github_link_lookup_failed",
            stage="credit",
            workspace_id=str(workspace_id),
            error_type=type(exc).__name__,
        )
        return CreditDecision("unresolved", None, LOOKUP_FAILED)
    return CreditDecision("credited", recorded_user, None)


async def finalize_external_attribution(
    session: AsyncSession,
    document_id: uuid.UUID,
    decision: CreditDecision,
) -> None:
    """Record the final outcome on every link of the document and the document.

    Must run after ``backfill_artifact_links`` so the clones are included, in
    the same transaction as the creation attribution. Idempotent.
    """
    resolved = str(decision.user_id) if decision.status == "credited" else None
    await session.execute(
        text(
            "UPDATE artifact_links SET resolved_user_id = CAST(:uid AS uuid) "
            "WHERE document_id = CAST(:doc AS uuid)"
        ),
        {"doc": str(document_id), "uid": resolved},
    )
    # The DB column is `metadata`; `pipeline_data` is only the ORM name.
    await session.execute(
        text(
            "UPDATE documents SET metadata = jsonb_set(metadata, "
            "'{attribution,final}', CAST(:final AS jsonb)) "
            "WHERE id = CAST(:doc AS uuid) AND metadata -> 'attribution' IS NOT NULL"
        ),
        {"doc": str(document_id), "final": json.dumps(decision.final_record())},
    )
    log.info(
        "external_attribution_finalized",
        document_id=str(document_id),
        status=decision.status,
        reason=decision.reason,
    )
