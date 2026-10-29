"""Write graph projection events to ``graph_outbox`` in the caller's transaction.

``emit_memory_states`` is called by every code path that changes a memory, its
contributions or its relations, **before** the commit that makes the change
durable (never inside a ``begin_nested`` block, and never after the commit). The
event rows then commit atomically with the data: the graph never sees a version
Postgres rolled back, and a crash between commit and publish loses nothing,
because a separate relay publishes unpublished rows.

One event is the full current state of one memory, read inside the open
transaction so it includes the caller's own uncommitted writes and runs under the
caller's row-level-security context. Events carry ids, versions and weights
only: never ``content``, embeddings, emails or display names.

Gated by ``ff_kafka_events`` (default off), so merging this changes nothing until
the flag is turned on; enabling it later is followed by a backfill.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.config import get_settings
from sourcemind.events.envelope import EVENT_MEMORY_STATE, dumps

log = structlog.get_logger(__name__)

PAYLOAD_VERSION = 1

# Advisory-lock namespace for emit/backfill ordering (distinct from the relay's session lock).
EMIT_LOCK_NAMESPACE = "graph_emit"


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _unique(ids: Iterable[uuid.UUID | str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in ids:
        seen.setdefault(str(value), None)
    return list(seen)


async def _lock_workspaces(session: AsyncSession, ids: list[str], *, exclusive: bool) -> None:
    """Serialise snapshot capture with concurrent emitters, per workspace, until commit.

    An event's ``seq`` is assigned at INSERT, and the projector keeps whichever event has the
    higher ``seq``. Without this lock, a backfill could read a memory, a concurrent writer could
    commit a newer state and its event, and the backfill's older snapshot would then be inserted
    with a higher ``seq`` and win. Writers take the lock shared (they never block each other);
    the backfill takes it exclusive, so a batch starts only after every in-flight writer has
    committed, and a writer that arrives mid-batch loads its state after the batch's commit and
    so gets the higher ``seq``. Every emit goes through here, including handoff-only and
    relation-only changes that never touch the ``memories`` row, so a row lock would not do.

    Workspaces are locked in sorted order. Invisible or missing memories contribute no lock:
    there is nothing to emit for them.
    """
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT workspace_id::text AS workspace_id FROM memories "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": ids},
        )
    ).mappings().all()
    function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    for workspace_id in sorted({row["workspace_id"] for row in rows}):
        await session.execute(
            text(f"SELECT {function}(hashtextextended(:key, 0))"),
            {"key": f"{EMIT_LOCK_NAMESPACE}:{workspace_id}"},
        )


async def _neighbours(session: AsyncSession, ids: list[str]) -> list[str]:
    rows = (
        await session.execute(
            text(
                "SELECT target_memory_id::text AS id FROM memory_relations "
                "WHERE source_memory_id = ANY(CAST(:ids AS uuid[])) "
                "UNION "
                "SELECT source_memory_id::text FROM memory_relations "
                "WHERE target_memory_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": ids},
        )
    ).mappings().all()
    known = set(ids)
    return [row["id"] for row in rows if row["id"] not in known]


async def load_memory_states(
    session: AsyncSession, memory_ids: list[str], reason: str
) -> list[dict[str, Any]]:
    """Return one payload per visible memory in ``memory_ids``. Pure reads; no content columns."""
    if not memory_ids:
        return []
    params = {"ids": memory_ids}

    memories = (
        await session.execute(
            text(
                "SELECT id::text AS id, workspace_id::text AS workspace_id, version, "
                "parent_memory_id::text AS parent_id, current_version, "
                "deleted_at IS NOT NULL AS deleted, deleted_reason, "
                "source_stale_at IS NOT NULL AS stale, COALESCE(importance_score, 0) AS importance "
                "FROM memories WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            params,
        )
    ).mappings().all()

    # Latest snapshot per (memory, user). Weights are NOT normalised (a handoff
    # transfer adds a share without reducing the departing one) and trigger_action
    # is an open string, so neither is interpreted here.
    contributors = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (memory_id, user_id) memory_id::text AS memory_id, "
                "user_id::text AS user_id, contribution_weight AS weight, "
                "trigger_action AS action, edit_id::text AS edit_id "
                "FROM attributions WHERE memory_id = ANY(CAST(:ids AS uuid[])) "
                "ORDER BY memory_id, user_id, created_at DESC, id DESC"
            ),
            params,
        )
    ).mappings().all()

    relations = (
        await session.execute(
            text(
                "SELECT id::text AS id, source_memory_id::text AS source, "
                "target_memory_id::text AS target, relation_type AS type, confidence "
                "FROM memory_relations "
                "WHERE source_memory_id = ANY(CAST(:ids AS uuid[])) "
                "OR target_memory_id = ANY(CAST(:ids AS uuid[]))"
            ),
            params,
        )
    ).mappings().all()

    wanted = set(memory_ids)
    states: list[dict[str, Any]] = []
    for row in memories:
        mid = row["id"]
        states.append(
            {
                "workspace_id": row["workspace_id"],
                "payload": {
                    "payload_version": PAYLOAD_VERSION,
                    "reason": reason,
                    "memory": {
                        "id": mid,
                        "version": row["version"],
                        "parent_id": row["parent_id"],
                        "current": bool(row["current_version"]),
                        "deleted": bool(row["deleted"]),
                        "deleted_reason": row["deleted_reason"],
                        "stale": bool(row["stale"]),
                        "importance": float(row["importance"] or 0.0),
                    },
                    "contributors": [
                        {
                            "user_id": c["user_id"],
                            "weight": float(c["weight"]),
                            "action": c["action"],
                            "edit_id": c["edit_id"],
                        }
                        for c in contributors
                        if c["memory_id"] == mid
                    ],
                    # Outgoing set: authoritative, replaced wholesale by the projector.
                    "relations_out": [
                        {
                            "id": r["id"],
                            "target": r["target"],
                            "type": r["type"],
                            "confidence": _optional_float(r["confidence"]),
                        }
                        for r in relations
                        if r["source"] == mid
                    ],
                    # Incoming edges: additive hints. An edit copies edges that pointed at the
                    # old version so they also point at the new one, which changes the OTHER
                    # memory's outgoing set without that memory being re-emitted.
                    "relations_in": [
                        {
                            "id": r["id"],
                            "source": r["source"],
                            "type": r["type"],
                            "confidence": _optional_float(r["confidence"]),
                        }
                        for r in relations
                        if r["target"] == mid and r["source"] not in wanted
                    ],
                },
            }
        )
    return states


async def emit_workspace_marker(
    session: AsyncSession, workspace_id: uuid.UUID | str, event_type: str, payload: dict[str, Any]
) -> int:
    """Insert a workspace-level control event and return its ``seq``.

    Used by the backfill to bracket a full re-emit. The runtime role may INSERT into the outbox
    but not read it, so the ``seq`` comes from ``currval`` of the sequence it was just allocated
    from (the role has USAGE on it), which is this session's own last insert.
    """
    await session.execute(
        text(
            "INSERT INTO graph_outbox (workspace_id, event_type, aggregate_id, payload) "
            "VALUES (CAST(:ws AS uuid), :type, CAST(:ws AS uuid), CAST(:payload AS jsonb))"
        ),
        {"ws": str(workspace_id), "type": event_type, "payload": dumps(payload).decode()},
    )
    seq = (await session.execute(text("SELECT currval('graph_outbox_seq_seq')"))).scalar()
    return int(seq)


async def emit_memory_states(
    session: AsyncSession,
    memory_ids: Iterable[uuid.UUID | str],
    *,
    reason: str,
    include_neighbours: bool = False,
    force: bool = False,
) -> int:
    """Insert one outbox row per memory. Returns how many were written (0 when the flag is off).

    Call inside the transaction, before its commit. ``include_neighbours`` also emits the
    memories linked to these by a relation, for paths that change their importance. ``force``
    bypasses the flag and is for the backfill, which is run on purpose.
    """
    if not (force or get_settings().ff_kafka_events):
        return 0
    ids = _unique(memory_ids)
    if not ids:
        return 0
    await _lock_workspaces(session, ids, exclusive=force)
    if include_neighbours:
        ids = _unique([*ids, *await _neighbours(session, ids)])

    states = await load_memory_states(session, ids, reason)
    for state in states:
        memory_id = state["payload"]["memory"]["id"]
        await session.execute(
            text(
                "INSERT INTO graph_outbox (workspace_id, event_type, aggregate_id, payload) "
                "VALUES (CAST(:ws AS uuid), :type, CAST(:agg AS uuid), CAST(:payload AS jsonb))"
            ),
            {
                "ws": state["workspace_id"],
                "type": EVENT_MEMORY_STATE,
                "agg": memory_id,
                "payload": dumps(state["payload"]).decode(),
            },
        )
    skipped = len(ids) - len(states)
    if skipped:
        # Not visible under the caller's row-level-security context (or gone): nothing to project.
        log.warning("graph.outbox_skipped_invisible", requested=len(ids), skipped=skipped)
    log.debug("graph.outbox_emitted", reason=reason, events=len(states))
    return len(states)
