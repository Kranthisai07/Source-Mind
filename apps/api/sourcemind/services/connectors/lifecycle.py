"""Connector state transitions (DB only): create, pause/resume, disconnect, retry, re-scan.

Every function runs inside the caller's transaction (the caller holds the workspace RLS
context and commits). Fence and generation bumps are single UPDATE statements so a running
sync worker, which renews its lease with ``fence = token AND status = 'active'``, loses it
the moment this transaction commits (contract C5, C8).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.connectors.github.contracts import SCOPES
from sourcemind.models.connector import ConnectorConfig
from sourcemind.schemas.connector import GitHubConnectorConfig

log = structlog.get_logger(__name__)

RETRYABLE_CODES = (
    "transient", "incomplete_extraction", "budget", "connector_disconnected", "connector_paused",
)
RETRY_BATCH_LIMIT = 200

RetireFn = Callable[..., Awaitable[Any]]
RequeueFn = Callable[..., Awaitable[bool]]


class LifecycleConflictError(Exception):
    """The requested transition is not allowed from the current state. ``code`` is stable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DisconnectResult:
    generation: int
    documents_considered: int = 0
    memories_retired: int = 0
    memories_flagged: int = 0


@dataclass(frozen=True)
class RetryResult:
    eligible: int
    requeued: int
    refused: int
    remaining: int


def scopes_for(config: dict[str, Any]) -> list[str]:
    selected = config.get("artifact_types")
    return list(selected) if selected else list(SCOPES)


async def create_connector(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    display_name: str,
    config: GitHubConnectorConfig,
    installation_id: int,
    authorized_github_user_id: int,
    repos: list[str],
) -> ConnectorConfig:
    """Insert the connector with the server-set installation binding."""
    stored = config.model_dump(exclude_none=True)
    stored["repos"] = repos
    connector = ConnectorConfig(
        workspace_id=workspace_id,
        connector_type="github",
        display_name=display_name,
        config=stored,
        status="active",
        installation_id=installation_id,
        authorized_github_user_id=authorized_github_user_id,
        created_by=created_by,
    )
    session.add(connector)
    await session.flush()
    await session.refresh(connector)
    return connector


async def _lock(session: AsyncSession, connector_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT id, workspace_id, status, generation FROM connector_configs "
                "WHERE id = :id FOR UPDATE"
            ),
            {"id": connector_id},
        )
    ).mappings().one()
    return dict(row)


async def pause(session: AsyncSession, connector_id: uuid.UUID) -> None:
    """Stop scheduling and kill any running lease (fence bump)."""
    row = await _lock(session, connector_id)
    if row["status"] == "disconnected":
        raise LifecycleConflictError("connector_disconnected")
    await session.execute(
        text(
            "UPDATE connector_configs SET status = 'paused', fence = fence + 1, "
            "next_sync_at = NULL, sync_lease_owner = NULL, sync_lease_expires_at = NULL "
            "WHERE id = :id"
        ),
        {"id": connector_id},
    )


async def resume(session: AsyncSession, connector_id: uuid.UUID, *, now: datetime) -> None:
    row = await _lock(session, connector_id)
    if row["status"] == "disconnected":
        raise LifecycleConflictError("connector_disconnected")
    await session.execute(
        text(
            "UPDATE connector_configs SET status = 'active', next_sync_at = :now "
            "WHERE id = :id"
        ),
        {"id": connector_id, "now": now},
    )


async def disconnect(
    session: AsyncSession,
    connector_id: uuid.UUID,
    *,
    remove_memories: bool,
    retire: RetireFn,
    now: datetime | None = None,
) -> DisconnectResult:
    """Soft disconnect (C8), optionally retiring imported memories (C9).

    One transaction: status, generation and fence change together, the schedule and lease
    are cleared and the installation binding is dropped. The row, logs, cursors and links
    stay. ``retire`` is ``retire_document_memories``; human-touched lineages are flagged
    by it, never retired.
    """
    now = now or datetime.now(UTC)
    row = await _lock(session, connector_id)
    workspace_id = row["workspace_id"]
    generation = (
        await session.execute(
            text(
                "UPDATE connector_configs SET status = 'disconnected', "
                "generation = generation + 1, fence = fence + 1, next_sync_at = NULL, "
                "sync_lease_owner = NULL, sync_lease_expires_at = NULL, "
                "installation_id = NULL, authorized_github_user_id = NULL "
                "WHERE id = :id RETURNING generation"
            ),
            {"id": connector_id},
        )
    ).scalar_one()

    considered = retired = flagged = 0
    if remove_memories:
        document_ids = (
            await session.execute(
                text(
                    "SELECT id FROM documents WHERE connector_id = :cid "
                    "AND workspace_id = :ws ORDER BY id"
                ),
                {"cid": connector_id, "ws": workspace_id},
            )
        ).scalars().all()
        for document_id in document_ids:
            outcome = await retire(
                session,
                workspace_id=workspace_id,
                document_id=document_id,
                reason="connector_removed",
                now=now,
            )
            considered += 1
            retired += len(outcome.retired_ids)
            flagged += len(outcome.flagged_ids)
    log.info(
        "connector_disconnected",
        connector_id=str(connector_id),
        generation=generation,
        remove_memories=remove_memories,
        documents=considered,
    )
    return DisconnectResult(int(generation), considered, retired, flagged)


async def retry_failed(
    session: AsyncSession,
    connector_id: uuid.UUID,
    *,
    requeue: RequeueFn,
    now: datetime | None = None,
    limit: int = RETRY_BATCH_LIMIT,
) -> RetryResult:
    """Re-arm failed documents of the CURRENT generation that are retryable.

    Attempts are reset to zero first (this is an explicit operator action), then the
    receiver's ``requeue_transient_failure`` decides: it still refuses when the connector
    is no longer active at the document's generation.
    """
    now = now or datetime.now(UTC)
    row = await _lock(session, connector_id)
    if row["status"] == "disconnected":
        raise LifecycleConflictError("connector_disconnected")
    workspace_id = row["workspace_id"]
    base = (
        "FROM documents WHERE connector_id = :cid AND workspace_id = :ws "
        "AND connector_generation = :gen AND ingestion_status = 'failed' "
        "AND deleted_at IS NULL AND metadata ->> 'error_code' = ANY(:codes)"
    )
    params = {
        "cid": connector_id,
        "ws": workspace_id,
        "gen": row["generation"],
        "codes": list(RETRYABLE_CODES),
    }
    total = (await session.execute(text(f"SELECT count(*) {base}"), params)).scalar_one()  # noqa: S608
    ids = (
        await session.execute(
            text(f"SELECT id {base} ORDER BY created_at, id LIMIT :lim FOR UPDATE"),  # noqa: S608
            {**params, "lim": limit},
        )
    ).scalars().all()
    requeued = 0
    for document_id in ids:
        await session.execute(
            text(
                "UPDATE documents SET metadata = (metadata - 'next_retry_at') "
                "|| jsonb_build_object('attempts', 0) WHERE id = :id"
            ),
            {"id": document_id},
        )
        if await requeue(session, document_id, workspace_id, now=now):
            requeued += 1
    return RetryResult(
        eligible=int(total),
        requeued=requeued,
        refused=len(ids) - requeued,
        remaining=max(0, int(total) - len(ids)),
    )


async def rescan(
    session: AsyncSession, connector_id: uuid.UUID, *, config: dict[str, Any], now: datetime
) -> int:
    """Restart every scope from the import-window start and schedule a run now.

    This does not detect deletions.

    The sync package executes the pass: a scope with ``phase='backfill'``,
    ``listing_complete=false`` and no resume key is traversed from its window start and
    its inventory would feed the two-scan deletion rule (C7), which is inert today because no scope
    reports a full inventory. Missing cursor rows are created.
    Cursor rows of disconnected/paused connectors are not touched.
    """
    row = await _lock(session, connector_id)
    if row["status"] in ("disconnected", "paused"):
        raise LifecycleConflictError(f"connector_{row['status']}")
    marked = 0
    for repo in config.get("repos", []):
        for scope in scopes_for(config):
            await session.execute(
                text(
                    "INSERT INTO connector_sync_cursors "
                    "(connector_id, workspace_id, repo, artifact_type, phase, "
                    " listing_complete, status, updated_at) "
                    "VALUES (:cid, :ws, :repo, :scope, 'backfill', false, 'ok', :now) "
                    "ON CONFLICT (connector_id, repo, artifact_type) DO UPDATE SET "
                    " phase = 'backfill', listing_complete = false, resume_ts = NULL, "
                    " resume_id = NULL, window_low = NULL, window_high = NULL, "
                    " head_sha = NULL, consumed = 0, updated_at = :now"
                ),
                {
                    "cid": connector_id,
                    "ws": row["workspace_id"],
                    "repo": repo,
                    "scope": scope,
                    "now": now,
                },
            )
            marked += 1
    await session.execute(
        text("UPDATE connector_configs SET next_sync_at = :now WHERE id = :id"),
        {"id": connector_id, "now": now},
    )
    return marked


@dataclass(frozen=True)
class RearmResult:
    rearmed: int
    scopes_marked: int


async def rearm_quarantined(
    session: AsyncSession, connector_id: uuid.UUID, *, now: datetime
) -> RearmResult:
    """Give quarantined work items a fresh budget and make discovery see them again.

    A quarantined item was skipped and the scope's resume key moved past it, so flipping the row
    back to ``pending`` is not enough: nothing would look at it again. The scopes that hold
    quarantined items are therefore restarted from the import-window start (as a re-scan does) and a
    run is scheduled. Everything stays in the database (state, cursors, schedule), so a crash or a
    redeploy between this call and the next sync loses nothing. Publication cannot duplicate: an item
    whose revision is already the artifact's latest hands off as ``same_revision`` and only the
    item that really failed produces a new revision. Locked like every lifecycle change.
    """
    row = await _lock(session, connector_id)
    if row["status"] in ("disconnected", "paused"):
        raise LifecycleConflictError(f"connector_{row['status']}")
    items = (
        await session.execute(
            text(
                "UPDATE connector_work_items SET state = 'pending', attempts = 0, "
                "last_error_code = NULL, updated_at = :now "
                "WHERE connector_id = :id AND state = 'quarantined' "
                "RETURNING repo, artifact_type"
            ),
            {"id": connector_id, "now": now},
        )
    ).all()
    scopes = {(r[0], r[1]) for r in items}
    for repo, scope in scopes:
        await session.execute(
            text(
                "INSERT INTO connector_sync_cursors "
                "(connector_id, workspace_id, repo, artifact_type, phase, "
                " listing_complete, status, updated_at) "
                "VALUES (:cid, :ws, :repo, :scope, 'backfill', false, 'ok', :now) "
                "ON CONFLICT (connector_id, repo, artifact_type) DO UPDATE SET "
                " phase = 'backfill', listing_complete = false, resume_ts = NULL, "
                " resume_id = NULL, window_low = NULL, window_high = NULL, "
                " head_sha = NULL, consumed = 0, updated_at = :now"
            ),
            {"cid": connector_id, "ws": row["workspace_id"], "repo": repo, "scope": scope,
             "now": now},
        )
    if items:
        await session.execute(
            text("UPDATE connector_configs SET next_sync_at = :now WHERE id = :id AND "
                 "status = 'active'"),
            {"id": connector_id, "now": now},
        )
    return RearmResult(rearmed=len(items), scopes_marked=len(scopes))
