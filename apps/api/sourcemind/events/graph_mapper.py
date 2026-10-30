"""Turn one graph event into Neo4j writes. Pure apart from the injected ``run`` callable.

The event is the full current state of one memory (see events/envelope.py), so applying it is
idempotent and order-tolerant:

* a memory node is claimed only if the event's ``seq`` is newer than the node's, so a
  duplicate or a stale event does nothing;
* contributions and the memory's outgoing relations are replaced wholesale, not merged;
* incoming relations are additive hints (an edit copies edges that pointed at the old
  version, which changes another memory's outgoing set without that memory being re-emitted).

Tenancy: Neo4j Community has no row-level security. Every statement below filters or keys on
``$workspace_id``, node keys embed the workspace (``"<workspace_id>:<id>"``), and the tests
assert both. Cypher is constant text; every value travels as a parameter.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from sourcemind.events.envelope import (
    EVENT_BACKFILL_COMPLETE,
    EVENT_BACKFILL_STARTED,
    EVENT_MEMORY_STATE,
    GraphEvent,
)

log = structlog.get_logger(__name__)

Run = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]


class InvalidEventError(ValueError):
    """The event is well-formed JSON but its payload cannot be applied."""


# A memory node is created on first sight (as a stub with seq 0, so an event for another
# memory that mentions it does not have to wait) and claimed by the first event newer than it.
CLAIM_MEMORY = """
MERGE (m:Memory {key: $mkey})
  ON CREATE SET m.workspace_id = $workspace_id, m.id = $memory_id, m.seq = 0
WITH m
WHERE m.workspace_id = $workspace_id AND m.seq < $seq
SET m.version = $version, m.parent_id = $parent_id, m.current = $current,
    m.deleted = $deleted, m.deleted_reason = $deleted_reason, m.stale = $stale,
    m.importance = $importance, m.seq = $seq
RETURN m.key AS key
"""

DELETE_PARENT = """
MATCH (m:Memory {key: $mkey, workspace_id: $workspace_id})-[r:PARENT]->()
DELETE r
"""

MERGE_PARENT = """
MATCH (m:Memory {key: $mkey, workspace_id: $workspace_id})
MERGE (p:Memory {key: $pkey})
  ON CREATE SET p.workspace_id = $workspace_id, p.id = $parent_id, p.seq = 0
WITH m, p
WHERE p.workspace_id = $workspace_id
MERGE (m)-[:PARENT]->(p)
"""

DELETE_CONTRIBUTIONS = """
MATCH (:User)-[c:CONTRIBUTED]->(m:Memory {key: $mkey, workspace_id: $workspace_id})
DELETE c
"""

MERGE_CONTRIBUTIONS = """
UNWIND $rows AS row
MATCH (m:Memory {key: $mkey, workspace_id: $workspace_id})
MERGE (u:User {key: row.ukey})
  ON CREATE SET u.workspace_id = $workspace_id, u.id = row.user_id
WITH m, u, row
WHERE u.workspace_id = $workspace_id
MERGE (u)-[c:CONTRIBUTED]->(m)
SET c.weight = row.weight, c.action = row.action, c.edit_id = row.edit_id,
    c.workspace_id = $workspace_id
"""

DELETE_RELATIONS_OUT = """
MATCH (m:Memory {key: $mkey, workspace_id: $workspace_id})-[r:RELATES]->()
DELETE r
"""

MERGE_RELATIONS_OUT = """
UNWIND $rows AS row
MATCH (m:Memory {key: $mkey, workspace_id: $workspace_id})
MERGE (t:Memory {key: row.tkey})
  ON CREATE SET t.workspace_id = $workspace_id, t.id = row.target_id, t.seq = 0
WITH m, t, row
WHERE t.workspace_id = $workspace_id
MERGE (m)-[r:RELATES {id: row.id}]->(t)
SET r.type = row.type, r.confidence = row.confidence, r.workspace_id = $workspace_id
"""

MERGE_RELATIONS_IN = """
UNWIND $rows AS row
MATCH (t:Memory {key: $mkey, workspace_id: $workspace_id})
MERGE (s:Memory {key: row.skey})
  ON CREATE SET s.workspace_id = $workspace_id, s.id = row.source_id, s.seq = 0
WITH t, s, row
WHERE s.workspace_id = $workspace_id
MERGE (s)-[r:RELATES {id: row.id}]->(t)
SET r.type = row.type, r.confidence = row.confidence, r.workspace_id = $workspace_id
"""

# The projector's high-water mark per workspace: lets the API tell a fresh graph from a lagging one.
ADVANCE_WATERMARK = """
MERGE (w:Workspace {key: $workspace_id})
SET w.applied_seq = CASE WHEN coalesce(w.applied_seq, 0) < $seq THEN $seq ELSE w.applied_seq END
"""

# A workspace whose event was dead-lettered is missing state the watermark would otherwise hide
# (a later event lifts applied_seq past it). The gap is sticky: reads must not trust the graph
# until a backfill that started after the lost event has been fully applied (CLEAR_GAP).
MARK_GAP = """
MERGE (w:Workspace {key: $workspace_id})
SET w.gap_seq = CASE WHEN coalesce(w.gap_seq, 0) < $seq THEN $seq ELSE w.gap_seq END
"""

CLEAR_GAP = """
MATCH (w:Workspace {key: $workspace_id})
WHERE w.gap_seq IS NOT NULL AND w.gap_seq < $started_seq
REMOVE w.gap_seq
"""

# A workspace's graph is complete only once a full re-emit of it has been applied. Events carry
# one memory each, so a workspace that already had data when the pipeline was switched on would
# otherwise look caught up after its first new event while missing everything older.
BASELINE = """
MATCH (w:Workspace {key: $workspace_id})
SET w.baseline_seq = CASE
  WHEN coalesce(w.baseline_seq, 0) < $started_seq THEN $started_seq ELSE w.baseline_seq END
"""

ALL_STATEMENTS = (
    CLAIM_MEMORY,
    DELETE_PARENT,
    MERGE_PARENT,
    DELETE_CONTRIBUTIONS,
    MERGE_CONTRIBUTIONS,
    DELETE_RELATIONS_OUT,
    MERGE_RELATIONS_OUT,
    MERGE_RELATIONS_IN,
    ADVANCE_WATERMARK,
    MARK_GAP,
    CLEAR_GAP,
    BASELINE,
)


def _uuid(value: Any, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidEventError(f"{what} is not a UUID") from exc


def _key(workspace_id: str, item_id: str) -> str:
    return f"{workspace_id}:{item_id}"


async def mark_gap(run: Run, workspace_id: str, seq: int) -> None:
    """Record that event ``seq`` of this workspace was lost; reads then stop trusting the graph."""
    await run(MARK_GAP, {"workspace_id": _uuid(workspace_id, "workspace_id"), "seq": int(seq)})


async def _apply_backfill_marker(run: Run, event: GraphEvent) -> bool:
    workspace_id = _uuid(event.workspace_id, "workspace_id")
    base = {"workspace_id": workspace_id, "seq": event.seq}
    started = None
    if event.type == EVENT_BACKFILL_COMPLETE:
        started = event.payload.get("started_seq")
        if not isinstance(started, int) or isinstance(started, bool) or started < 1:
            raise InvalidEventError("payload.started_seq must be a positive integer")
    # The marker is an ordinary published event, so the watermark accounts for it too (this also
    # creates the workspace node the statements below match).
    await run(ADVANCE_WATERMARK, base)
    if started is not None:
        # Only gaps older than the backfill's own start are repaired by it. A backfill event that
        # was itself dead-lettered recorded a newer gap, which this leaves in place.
        await run(CLEAR_GAP, {**base, "started_seq": started})
        await run(BASELINE, {**base, "started_seq": started})
    return True


async def apply_event(run: Run, event: GraphEvent) -> bool:
    """Apply one event through ``run``. Returns False when it was stale or not for this code.

    Must be called inside a single Neo4j transaction: either every statement applies or none.
    """
    if event.type in (EVENT_BACKFILL_STARTED, EVENT_BACKFILL_COMPLETE):
        return await _apply_backfill_marker(run, event)
    if event.type != EVENT_MEMORY_STATE:
        log.warning("graph.event_type_ignored", type=event.type)
        return False

    workspace_id = _uuid(event.workspace_id, "workspace_id")
    memory = event.payload.get("memory")
    if not isinstance(memory, dict):
        raise InvalidEventError("payload.memory missing")
    memory_id = _uuid(memory.get("id"), "memory.id")
    parent_id = _uuid(memory["parent_id"], "memory.parent_id") if memory.get("parent_id") else None

    base = {
        "workspace_id": workspace_id,
        "mkey": _key(workspace_id, memory_id),
        "memory_id": memory_id,
        "seq": event.seq,
    }

    claimed = await run(
        CLAIM_MEMORY,
        {
            **base,
            "version": int(memory.get("version") or 1),
            "parent_id": parent_id,
            "current": bool(memory.get("current")),
            "deleted": bool(memory.get("deleted")),
            "deleted_reason": memory.get("deleted_reason"),
            "stale": bool(memory.get("stale")),
            "importance": float(memory.get("importance") or 0.0),
        },
    )
    if not claimed:
        return False  # a newer event already holds this memory

    await run(DELETE_PARENT, base)
    if parent_id:
        await run(
            MERGE_PARENT,
            {**base, "pkey": _key(workspace_id, parent_id), "parent_id": parent_id},
        )

    await run(DELETE_CONTRIBUTIONS, base)
    contributions = []
    for item in event.payload.get("contributors") or []:
        user_id = _uuid(item.get("user_id"), "contributor.user_id")
        contributions.append(
            {
                "ukey": _key(workspace_id, user_id),
                "user_id": user_id,
                "weight": float(item.get("weight") or 0.0),
                "action": item.get("action"),
                "edit_id": item.get("edit_id"),
            }
        )
    if contributions:
        await run(MERGE_CONTRIBUTIONS, {**base, "rows": contributions})

    await run(DELETE_RELATIONS_OUT, base)
    outgoing = []
    for item in event.payload.get("relations_out") or []:
        target_id = _uuid(item.get("target"), "relation.target")
        outgoing.append(
            {
                "id": _uuid(item.get("id"), "relation.id"),
                "tkey": _key(workspace_id, target_id),
                "target_id": target_id,
                "type": item.get("type"),
                "confidence": item.get("confidence"),
            }
        )
    if outgoing:
        await run(MERGE_RELATIONS_OUT, {**base, "rows": outgoing})

    incoming = []
    for item in event.payload.get("relations_in") or []:
        source_id = _uuid(item.get("source"), "relation.source")
        incoming.append(
            {
                "id": _uuid(item.get("id"), "relation.id"),
                "skey": _key(workspace_id, source_id),
                "source_id": source_id,
                "type": item.get("type"),
                "confidence": item.get("confidence"),
            }
        )
    if incoming:
        await run(MERGE_RELATIONS_IN, {**base, "rows": incoming})

    await run(ADVANCE_WATERMARK, base)
    return True
