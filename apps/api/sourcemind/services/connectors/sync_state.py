"""Lease/fence helpers and durable sync state (contract C3, C4, C5, C7).

Every write the sync orchestrator makes goes through a transaction that FIRST runs
``renew_lease`` (the fenced UPDATE). Zero rows means the worker lost the lease (it
expired and another worker took over, or the connector was disconnected/paused): the
transaction is rolled back and ``LeaseLostError`` stops the worker with no effect.

Lease timing uses the database clock (``now()``) so that workers with skewed clocks
agree on expiry.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Scope -> segment source types whose COMPLETE inventory that scope provides (C7).
#: Exclusive on purpose: ``pull_review`` is also emitted by ``pull_requests`` but only the
#: reconcile sweep lists reviews completely, so only it may declare a review missing.
SCOPE_INVENTORY_TYPES: dict[str, tuple[str, ...]] = {
    "commits": ("commit",),
    "pull_requests": ("pull_request",),
    "issues": ("issue",),
    "issue_comments": ("issue_comment",),
    "review_comments": ("pull_review_comment",),
    "review_reconcile": ("pull_review",),
}

_CURSOR_COLUMNS = frozenset(
    {
        "phase",
        "window_low",
        "window_high",
        "resume_ts",
        "resume_id",
        "listing_complete",
        "head_sha",
        "status",
        "last_error_code",
        "authorization_verified_at",
        "last_progress_at",
    }
)


class LeaseLostError(Exception):
    """The worker no longer holds the lease (fence mismatch or connector not active)."""


@dataclass(frozen=True)
class LeaseToken:
    connector_id: uuid.UUID
    fence: int
    owner: str


# ── lease / fence ─────────────────────────────────────────────────────────────


async def acquire_lease(
    session: AsyncSession,
    connector_id: uuid.UUID,
    *,
    lease_seconds: int,
    owner: str | None = None,
) -> LeaseToken | None:
    """Take the lease if it is free or expired; increments ``fence``. Commits."""
    owner = owner or uuid.uuid4().hex
    row = (
        await session.execute(
            text(
                "UPDATE connector_configs SET fence = fence + 1, "
                "sync_lease_owner = :owner, "
                "sync_lease_expires_at = now() + make_interval(secs => :secs) "
                "WHERE id = :id AND status = 'active' "
                "AND (sync_lease_expires_at IS NULL OR sync_lease_expires_at < now()) "
                "RETURNING fence"
            ),
            {"id": connector_id, "owner": owner, "secs": float(lease_seconds)},
        )
    ).first()
    if row is None:
        await session.rollback()
        return None
    await session.commit()
    return LeaseToken(connector_id=connector_id, fence=int(row[0]), owner=owner)


async def renew_lease(session: AsyncSession, token: LeaseToken, *, lease_seconds: int) -> None:
    """Fenced heartbeat; first statement of every write transaction."""
    row = (
        await session.execute(
            text(
                "UPDATE connector_configs "
                "SET sync_lease_expires_at = now() + make_interval(secs => :secs) "
                "WHERE id = :id AND fence = :fence AND status = 'active' RETURNING 1"
            ),
            {"id": token.connector_id, "fence": token.fence, "secs": float(lease_seconds)},
        )
    ).first()
    if row is None:
        await session.rollback()
        raise LeaseLostError(str(token.connector_id))


async def mark_stale_runs_interrupted(session: AsyncSession, connector_id: uuid.UUID) -> int:
    """Reaper: a ``running`` log left by a dead/superseded worker becomes ``interrupted``.

    Call inside a fenced transaction (after ``renew_lease``) by the new lease holder.
    """
    result = await session.execute(
        text(
            "UPDATE connector_sync_logs SET status = 'interrupted', completed_at = now(), "
            "error_code = COALESCE(error_code, 'interrupted') "
            "WHERE connector_id = :id AND status = 'running'"
        ),
        {"id": connector_id},
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def finish_run(
    session: AsyncSession,
    token: LeaseToken,
    *,
    next_sync_at: datetime,
    last_sync_at: datetime,
    success: bool,
    error_code: str | None,
    connector_status: str | None = None,
) -> None:
    """Fenced end-of-run update: schedule, outcome and lease release, in one statement."""
    row = (
        await session.execute(
            text(
                "UPDATE connector_configs SET next_sync_at = :next, last_sync_at = :last, "
                "last_success_at = CASE WHEN :ok THEN :last ELSE last_success_at END, "
                "last_error_code = :code, "
                "status = COALESCE(CAST(:status AS text), status), "
                "sync_lease_expires_at = NULL, sync_lease_owner = NULL "
                "WHERE id = :id AND fence = :fence AND status = 'active' RETURNING 1"
            ),
            {
                "id": token.connector_id,
                "fence": token.fence,
                "next": next_sync_at,
                "last": last_sync_at,
                "ok": success,
                "code": error_code,
                "status": connector_status,
            },
        )
    ).first()
    if row is None:
        await session.rollback()
        raise LeaseLostError(str(token.connector_id))


async def release_lease(session: AsyncSession, token: LeaseToken) -> None:
    """Best-effort release (no status condition: a paused/disconnected row is fine)."""
    await session.execute(
        text(
            "UPDATE connector_configs SET sync_lease_expires_at = NULL, sync_lease_owner = NULL "
            "WHERE id = :id AND fence = :fence"
        ),
        {"id": token.connector_id, "fence": token.fence},
    )
    await session.commit()


# ── cursors ───────────────────────────────────────────────────────────────────


async def load_cursors(
    session: AsyncSession, connector_id: uuid.UUID
) -> dict[tuple[str, str], dict[str, Any]]:
    rows = (
        await session.execute(
            text("SELECT * FROM connector_sync_cursors WHERE connector_id = :id"),
            {"id": connector_id},
        )
    ).mappings()
    return {(r["repo"], r["artifact_type"]): dict(r) for r in rows}


async def upsert_cursor(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connector_id: uuid.UUID,
    repo: str,
    scope: str,
    **fields: Any,
) -> None:
    unknown = set(fields) - _CURSOR_COLUMNS
    if unknown:
        raise ValueError(f"unknown cursor columns: {sorted(unknown)}")
    cols = sorted(fields)
    insert_cols = ", ".join(["connector_id", "workspace_id", "repo", "artifact_type", *cols])
    insert_vals = ", ".join(
        [":connector_id", ":workspace_id", ":repo", ":scope", *[f":{c}" for c in cols]]
    )
    updates = ", ".join([*[f"{c} = EXCLUDED.{c}" for c in cols], "updated_at = now()"])
    sql = (
        f"INSERT INTO connector_sync_cursors ({insert_cols}) VALUES ({insert_vals}) "  # noqa: S608
        f"ON CONFLICT (connector_id, repo, artifact_type) DO UPDATE SET {updates}"
    )
    await session.execute(
        text(sql),
        {
            "connector_id": connector_id,
            "workspace_id": workspace_id,
            "repo": repo,
            "scope": scope,
            **fields,
        },
    )


# ── work items (pre-commit handoff failures) ──────────────────────────────────


async def load_work_items(
    session: AsyncSession, connector_id: uuid.UUID
) -> dict[tuple[str, str, str], tuple[int, str]]:
    """(repo, scope, source_id) -> (attempts, state) for items not yet ``done``."""
    rows = await session.execute(
        text(
            "SELECT repo, artifact_type, source_id, attempts, state "
            "FROM connector_work_items WHERE connector_id = :id AND state <> 'done'"
        ),
        {"id": connector_id},
    )
    return {(r[0], r[1], r[2]): (int(r[3]), str(r[4])) for r in rows}


async def record_work_failure(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connector_id: uuid.UUID,
    repo: str,
    scope: str,
    source_id: str,
    error_code: str,
    max_attempts: int,
) -> tuple[int, str]:
    """Count one failed pre-commit handoff; quarantine at ``max_attempts``."""
    row = (
        await session.execute(
            text(
                "INSERT INTO connector_work_items "
                "(connector_id, workspace_id, repo, artifact_type, source_id, attempts, "
                " state, last_error_code) "
                "VALUES (:cid, :ws, :repo, :scope, :sid, 1, "
                " CASE WHEN 1 >= :max THEN 'quarantined' ELSE 'pending' END, :code) "
                "ON CONFLICT (connector_id, repo, artifact_type, source_id) DO UPDATE SET "
                " attempts = connector_work_items.attempts + 1, "
                " state = CASE WHEN connector_work_items.attempts + 1 >= :max "
                "              THEN 'quarantined' ELSE 'pending' END, "
                " last_error_code = :code, updated_at = now() "
                "RETURNING attempts, state"
            ),
            {
                "cid": connector_id,
                "ws": workspace_id,
                "repo": repo,
                "scope": scope,
                "sid": source_id,
                "code": error_code,
                "max": max_attempts,
            },
        )
    ).one()
    return int(row[0]), str(row[1])


async def mark_work_done(
    session: AsyncSession,
    *,
    connector_id: uuid.UUID,
    repo: str,
    scope: str,
    source_id: str,
) -> None:
    await session.execute(
        text(
            "UPDATE connector_work_items SET state = 'done', updated_at = now() "
            "WHERE connector_id = :cid AND repo = :repo AND artifact_type = :scope "
            "AND source_id = :sid AND state = 'pending'"
        ),
        {"cid": connector_id, "repo": repo, "scope": scope, "sid": source_id},
    )


# ── deletion rule bookkeeping (C7) ────────────────────────────────────────────


async def apply_inventory(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo: str,
    scope: str,
    seen_source_ids: Iterable[str],
    required_scans: int = 2,
) -> list[tuple[str, uuid.UUID | None]]:
    """Update ``missing_scans`` from one COMPLETE inventory; return heads now due for retirement.

    Present heads are reset to 0; absent heads of the same repo/segment types are
    incremented. Returned: ``(source_id, published_document_id)`` for heads with
    ``missing_scans >= required_scans`` that still have a published document.
    """
    types = list(SCOPE_INVENTORY_TYPES.get(scope, ()))
    if not types:
        return []
    prefix = f"{repo}/"
    seen = sorted(set(seen_source_ids))
    base = {
        "ws": workspace_id,
        "types": types,
        "prefix": prefix,
        "plen": len(prefix),
        "seen": seen,
    }
    await session.execute(
        text(
            "UPDATE artifact_heads SET missing_scans = 0, updated_at = now() "
            "WHERE workspace_id = :ws AND source_tool = 'github' "
            "AND source_type = ANY(:types) AND left(source_id, :plen) = :prefix "
            "AND source_id = ANY(:seen) AND missing_scans <> 0"
        ),
        base,
    )
    rows = await session.execute(
        text(
            "UPDATE artifact_heads SET missing_scans = missing_scans + 1, updated_at = now() "
            "WHERE workspace_id = :ws AND source_tool = 'github' "
            "AND source_type = ANY(:types) AND left(source_id, :plen) = :prefix "
            "AND NOT (source_id = ANY(:seen)) AND latest_seq > 0 "
            "RETURNING source_id, missing_scans, published_document_id"
        ),
        base,
    )
    return [
        (str(r[0]), r[2])
        for r in rows
        if int(r[1]) >= required_scans and r[2] is not None
    ]


async def head_document_for(
    session: AsyncSession, *, workspace_id: uuid.UUID, source_id: str
) -> list[uuid.UUID]:
    """Published document ids for a source id (any segment type), for a 410 retirement."""
    rows = await session.execute(
        text(
            "SELECT published_document_id FROM artifact_heads WHERE workspace_id = :ws "
            "AND source_tool = 'github' AND source_id = :sid "
            "AND published_document_id IS NOT NULL"
        ),
        {"ws": workspace_id, "sid": source_id},
    )
    return [r[0] for r in rows]
