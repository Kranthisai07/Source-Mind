"""Retire the memories of a superseded / deleted / removed source (contract C6, C7, C9, C10).

The signature is a CONTRACT shared by the publication step (ingestion worker), the
connector removal path and the re-scan deletion rule.

Rules (docs/architecture/AUTOMATIC_INGESTION_CONTRACT.md):

* only MACHINE-ONLY memories are retired (``deleted_at`` + ``deleted_reason``): a lineage
  that is a single row and never had a human edit;
* a lineage containing a human edit is NEVER retired; its current head gets
  ``source_stale_at`` / ``source_stale_reason`` and stays searchable;
* attribution and contribution rows are only read, never altered or hidden;
* idempotent: calling twice for the same document changes nothing the second time.

Lineage and "human edit"
------------------------
A lineage is the set of rows linked by ``parent_memory_id``. It is human-touched when it
has more than one row (a PATCH or re-apply appends a new version) or when any of its rows
has an ``attribution_edits`` event whose ``action_type`` is not ``create`` (the pipeline's
own creation event). Over-classifying as human-touched is the safe direction: the memory
is flagged instead of hidden.

Lock order (C10) and the deadlock-freedom argument
--------------------------------------------------
1. ``connector_configs`` row ``FOR SHARE``   (taken by the CALLER)
2. ``artifact_heads`` row ``FOR UPDATE``     (taken by the CALLER)
3. ``memories`` rows, ascending ``id``       (taken HERE)

This function takes none of 1 and 2: the publication worker, the connector removal path
and the re-scan rule already hold them and must call this inside that transaction.

An edit (PATCH / re-apply) locks exactly ONE memory row, the lineage head it edits, and
never holds another row lock while waiting for a second one, so it cannot take part in a
lock cycle with this function. Two retirements of the same document are serialized by the
caller's ``artifact_heads`` lock. The row lock mode is ``FOR NO KEY UPDATE``: it excludes
every writer that takes ``FOR UPDATE`` (the edit path) but, unlike ``FOR UPDATE``, does not
conflict with the ``FOR KEY SHARE`` that a foreign-key check on ``memory_relations`` takes
when an edit copies relations to a sibling row, which removes the one cycle that mode would
allow.

State read before the memory locks is never trusted. After locking, the document's rows
are re-read in a NEW statement (a fresh snapshot under READ COMMITTED) and any version an
edit committed while we waited is locked as well; this repeats until the locked set is
stable, and only then is the lineage classified and written.

``source_stale_*`` is written once: a head that is already flagged keeps its original
timestamp and reason (that is what makes a second call a no-op).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.services.graph.outbox import emit_memory_states

log = structlog.get_logger(__name__)

RetireReason = Literal["source_superseded", "source_deleted", "connector_removed"]

#: retire reason -> ``source_stale_reason`` written on a human-touched head.
STALE_REASON_FOR: dict[str, str] = {
    "source_superseded": "source_revised",
    "source_deleted": "source_deleted",
    "connector_removed": "connector_removed",
}

#: ``deleted_reason`` values written by source refresh (everything but a user delete).
SOURCE_RETIRE_REASONS: frozenset[str] = frozenset(STALE_REASON_FOR)


@dataclass(frozen=True)
class RetireOutcome:
    retired_ids: list[uuid.UUID] = field(default_factory=list)  # machine-only, now deleted
    flagged_ids: list[uuid.UUID] = field(default_factory=list)  # human-touched, now stale


async def _lock_document_memories(
    session: AsyncSession, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> list:
    """Lock every memory row of the document in ascending id order; return them.

    Each pass is a new statement, so a version committed by an edit we waited on is
    seen by the next pass. Stops when a pass finds nothing new.
    """
    locked: set[uuid.UUID] = set()
    while True:
        rows = (
            await session.execute(
                text(
                    "SELECT id, parent_memory_id, current_version, deleted_at, "
                    "source_stale_at FROM memories "
                    "WHERE workspace_id = CAST(:ws AS uuid) "
                    "AND document_id = CAST(:doc AS uuid) "
                    "ORDER BY id FOR NO KEY UPDATE"
                ),
                {"ws": str(workspace_id), "doc": str(document_id)},
            )
        ).all()
        ids = {r.id for r in rows}
        if ids <= locked:
            return list(rows)
        locked |= ids


def _components(rows: list) -> list[list]:
    """Group rows into lineages by ``parent_memory_id`` (union-find)."""
    parent: dict[uuid.UUID, uuid.UUID] = {r.id: r.id for r in rows}

    def find(x: uuid.UUID) -> uuid.UUID:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in rows:
        if r.parent_memory_id is not None and r.parent_memory_id in parent:
            parent[find(r.id)] = find(r.parent_memory_id)
    groups: dict[uuid.UUID, list] = {}
    for r in rows:
        groups.setdefault(find(r.id), []).append(r)
    return list(groups.values())


async def retire_document_memories(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    reason: RetireReason,
    now: datetime | None = None,
) -> RetireOutcome:
    """Retire (machine-only) or flag (human-touched) every memory of ``document_id``.

    The caller holds the connector and ``artifact_heads`` locks (module docstring) and
    commits. Safe to call again: already-retired rows and already-flagged heads are
    skipped, so a second call returns an empty outcome and writes nothing.
    """
    if reason not in STALE_REASON_FOR:
        raise ValueError(f"unknown retire reason: {reason!r}")
    when = now or datetime.now(UTC)

    rows = await _lock_document_memories(session, workspace_id, document_id)
    if not rows:
        return RetireOutcome()

    touched_ids = {
        r.memory_id
        for r in await session.execute(
            text(
                "SELECT DISTINCT memory_id FROM attribution_edits "
                "WHERE memory_id = ANY(CAST(:ids AS uuid[])) "
                "AND action_type <> 'create'"
            ),
            {"ids": [str(r.id) for r in rows]},
        )
    }

    to_retire: list[uuid.UUID] = []
    to_flag: list[uuid.UUID] = []
    for lineage in _components(rows):
        human_touched = (
            len(lineage) > 1
            or any(r.parent_memory_id is not None for r in lineage)
            or any(r.id in touched_ids for r in lineage)
        )
        for r in lineage:
            if r.deleted_at is not None or not r.current_version:
                continue
            if human_touched:
                if r.source_stale_at is None:
                    to_flag.append(r.id)
            else:
                to_retire.append(r.id)

    retired: list[uuid.UUID] = []
    flagged: list[uuid.UUID] = []
    if to_retire:
        retired = [
            r.id
            for r in await session.execute(
                text(
                    "UPDATE memories SET deleted_at = :now, deleted_reason = :reason "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) AND deleted_at IS NULL "
                    "RETURNING id"
                ),
                {"now": when, "reason": reason, "ids": [str(i) for i in to_retire]},
            )
        ]
    if to_flag:
        flagged = [
            r.id
            for r in await session.execute(
                text(
                    "UPDATE memories SET source_stale_at = :now, "
                    "source_stale_reason = :stale "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) AND deleted_at IS NULL "
                    "AND source_stale_at IS NULL RETURNING id"
                ),
                {
                    "now": when,
                    "stale": STALE_REASON_FOR[reason],
                    "ids": [str(i) for i in to_flag],
                },
            )
        ]

    # Graph projection (flag-gated). Written here so the worker, the connector deletion rule and
    # connector disconnect, which commit at different places, are all covered by one call.
    await emit_memory_states(session, [*retired, *flagged], reason=f"retire:{reason}")

    log.info(
        "source_memories_retired",
        document_id=str(document_id),
        reason=reason,
        retired=len(retired),
        flagged=len(flagged),
    )
    return RetireOutcome(retired_ids=sorted(retired), flagged_ids=sorted(flagged))
