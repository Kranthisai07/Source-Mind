"""Graph-backed reads, each with an identical-shape PostgreSQL fallback.

PostgreSQL stays the source of truth. A read is served from Neo4j only when ALL of these hold:

* ``ff_neo4j_attribution`` is on and a driver exists;
* the graph is fresh for the workspace: the projector's applied ``seq`` has reached the relay's
  published ``seq`` (``graph_publication_cursor``). A workspace with no cursor row has nothing
  projected, so it is never fresh;
* for memory-scoped reads, the graph's node for that memory agrees with PostgreSQL about its
  version, whether it is current, and whether it is deleted (read-your-writes for the case that
  matters: opening a memory right after editing it);
* the graph answer is complete (no placeholder nodes the projector has not filled in yet).

Anything else, including any exception from the graph, is logged and answered from PostgreSQL.
The caller never sees a graph failure. Authorisation is the caller's job and happens first: the
workspace id passed in is the one it authorised, and it alone scopes every graph query
(``run_read`` refuses a query without ``$workspace_id``). The graph stores ids and numbers only;
display names, previews and timestamps are read from PostgreSQL under the caller's row-level
security. Cypher is constant text; variable-length bounds cannot be parameters, so depth selects
among fixed queries.

Weights are never normalised or assumed to sum to 1 (a handoff transfer adds a share without
reducing the departing one), and the latest snapshot per (memory, user) is the contribution.
"""

# ruff: noqa: S608  (SQL is built only from constant fragments; every value is a bound parameter)

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.config import get_settings
from sourcemind.core.graph import graph_available, run_read
from sourcemind.schemas.graph import (
    CollaborationEdge,
    CollaborationNode,
    CollaborationResponse,
    Contributor,
    LineageResponse,
    LineageVersion,
    OwnerEntry,
    OwnersResponse,
    RelatedEdge,
    RelatedNode,
    RelatedResponse,
)

log = structlog.get_logger(__name__)

MIN_CONTRIBUTION_WEIGHT = 0.05  # the same noise floor who-would-know uses
OWNER_DECAY = 0.5  # a contribution one hop away counts half as much
OWNER_DEPTH = 2
OWNER_LIMIT = 20
CHAIN_LIMIT = 200
MAX_DEPTH = 3
DEFAULT_RELATED_LIMIT = 25
MAX_RELATED_LIMIT = 50
COLLAB_NODE_LIMIT = 200
COLLAB_EDGE_LIMIT = 500
RELATION_TYPES = ("updates", "extends", "derives", "contradicts", "references")


def _key(workspace_id: uuid.UUID | str, item_id: uuid.UUID | str) -> str:
    return f"{workspace_id}:{item_id}"


# ── Cypher (constant text) ───────────────────────────────────────────────────

WORKSPACE_WATERMARK = (
    "MATCH (w:Workspace {key: $workspace_id}) "
    "RETURN w.applied_seq AS seq, w.gap_seq AS gap_seq"
)

MEMORY_NODE_CYPHER = (
    "MATCH (m:Memory {key: $key, workspace_id: $workspace_id}) "
    "RETURN m.version AS version, m.current AS is_current, m.seq AS seq"
)

LINEAGE_CYPHER = """
MATCH p = (head:Memory {key: $key, workspace_id: $workspace_id})-[:PARENT*0..200]->(m:Memory)
WHERE m.workspace_id = $workspace_id
  AND ALL(x IN nodes(p) WHERE coalesce(x.deleted, false) = false)
WITH DISTINCT m
OPTIONAL MATCH (u:User)-[c:CONTRIBUTED]->(m)
WITH m, collect(CASE WHEN u IS NULL THEN NULL
                     ELSE {user_id: u.id, weight: c.weight, action: c.action} END) AS contributors
RETURN m.id AS id, m.version AS version, m.current AS is_current, m.seq AS seq, contributors
ORDER BY m.version DESC, m.id
LIMIT $limit
"""


def _related_cypher(depth: int) -> str:
    assert depth in (1, 2, 3)  # the only values ever substituted; never user text
    return f"""
MATCH p = (m:Memory {{key: $key, workspace_id: $workspace_id}})-[:RELATES*1..{depth}]-(n:Memory)
WHERE n.workspace_id = $workspace_id AND n.id <> m.id
  AND ALL(x IN nodes(p) WHERE coalesce(x.deleted, false) = false)
  AND ($types IS NULL OR ALL(r IN relationships(p) WHERE r.type IN $types))
WITH n, min(length(p)) AS hops
RETURN n.id AS id, n.version AS version, n.current AS is_current,
       n.importance AS importance, n.seq AS seq, hops
ORDER BY hops, id
LIMIT $limit
"""


RELATED_CYPHER = {depth: _related_cypher(depth) for depth in (1, 2, 3)}

RELATED_EDGES_CYPHER = """
MATCH (a:Memory {workspace_id: $workspace_id})-[r:RELATES {workspace_id: $workspace_id}]->
      (b:Memory {workspace_id: $workspace_id})
WHERE a.id IN $ids AND b.id IN $ids AND ($types IS NULL OR r.type IN $types)
RETURN r.id AS id, a.id AS source, b.id AS target, r.type AS type, r.confidence AS confidence
ORDER BY r.id
"""

OWNERS_CYPHER = """
MATCH p = (m:Memory {key: $key, workspace_id: $workspace_id})-[:RELATES*0..2]-(n:Memory)
WHERE n.workspace_id = $workspace_id
  AND ALL(x IN nodes(p) WHERE coalesce(x.deleted, false) = false)
WITH n, p ORDER BY length(p), [x IN nodes(p) | x.id]
WITH n, head(collect(p)) AS best
MATCH (u:User)-[c:CONTRIBUTED]->(n)
WHERE c.weight >= $min_weight
RETURN u.id AS user_id, n.id AS memory_id, c.weight AS weight, c.action AS action,
       length(best) AS hops, [x IN nodes(best) | x.id] AS path
"""

COLLAB_NODES_CYPHER = """
MATCH (u:User {workspace_id: $workspace_id})-[c:CONTRIBUTED {workspace_id: $workspace_id}]->
      (m:Memory {workspace_id: $workspace_id})
WHERE c.weight >= $min_weight AND coalesce(m.current, false) = true
  AND coalesce(m.deleted, false) = false
WITH u, count(DISTINCT m) AS memory_count
RETURN u.id AS user_id, memory_count
ORDER BY memory_count DESC, user_id
LIMIT $limit
"""

COLLAB_EDGES_CYPHER = """
MATCH (a:User {workspace_id: $workspace_id})-[ca:CONTRIBUTED {workspace_id: $workspace_id}]->
      (m:Memory {workspace_id: $workspace_id})<-[cb:CONTRIBUTED {workspace_id: $workspace_id}]-
      (b:User {workspace_id: $workspace_id})
WHERE a.id < b.id AND ca.weight >= $min_weight AND cb.weight >= $min_weight
  AND a.id IN $ids AND b.id IN $ids
  AND coalesce(m.current, false) = true AND coalesce(m.deleted, false) = false
RETURN a.id AS source, b.id AS target, count(DISTINCT m) AS shared_memories
ORDER BY shared_memories DESC, source, target
LIMIT $limit
"""


# ── Shared plumbing ──────────────────────────────────────────────────────────


def graph_usable() -> bool:
    return bool(get_settings().ff_neo4j_attribution) and graph_available()


async def _attempt_graph[T](
    session: AsyncSession, endpoint: str, attempt: Callable[[], Awaitable[T | None]]
) -> T | None:
    """Run a graph attempt inside a savepoint. Returns None to mean "answer from PostgreSQL".

    The savepoint matters: a failed statement would otherwise abort the caller's transaction and
    take the fallback down with it.
    """
    if not graph_usable():
        return None
    try:
        async with session.begin_nested():
            return await attempt()
    except Exception as exc:
        log.warning("graph.read_failed", endpoint=endpoint, error=type(exc).__name__)
        return None


async def _graph_fresh(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    cursor = (
        (
            await session.execute(
                text(
                    "SELECT published_seq FROM graph_publication_cursor "
                    "WHERE workspace_id = CAST(:ws AS uuid)"
                ),
                {"ws": str(workspace_id)},
            )
        )
        .mappings()
        .first()
    )
    if cursor is None:
        return False  # nothing has been published for this workspace, so nothing is projected
    rows = await run_read(WORKSPACE_WATERMARK, str(workspace_id))
    applied = rows[0]["seq"] if rows else None
    if rows and rows[0].get("gap_seq") is not None:
        # An event was dead-lettered and no backfill has repaired it since: the watermark can be
        # past it, so "caught up" proves nothing. PostgreSQL answers until the backfill completes.
        return False
    return applied is not None and int(applied) >= int(cursor["published_seq"])


async def _memory_rows(
    session: AsyncSession, workspace_id: uuid.UUID, ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Version, flags, importance, a short preview and created_at, for visible memories only."""
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id::text AS id, version, current_version, "
                    "deleted_at IS NOT NULL AS deleted, "
                    "COALESCE(importance_score, 0) AS importance, created_at, "
                    "left(regexp_replace(content, '\\s+', ' ', 'g'), 120) AS preview "
                    "FROM memories WHERE workspace_id = CAST(:ws AS uuid) "
                    "AND id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ws": str(workspace_id), "ids": list(ids)},
            )
        )
        .mappings()
        .all()
    )
    return {row["id"]: dict(row) for row in rows}


async def _display_names(session: AsyncSession, user_ids: Sequence[str]) -> dict[str, str | None]:
    if not user_ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id::text AS id, display_name FROM users "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(user_ids)},
            )
        )
        .mappings()
        .all()
    )
    return {row["id"]: row["display_name"] for row in rows}


async def _latest_contributions(
    session: AsyncSession, memory_ids: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """Latest snapshot per (memory, user). Ties broken by id so the answer is deterministic."""
    if not memory_ids:
        return {}
    rows = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (memory_id, user_id) memory_id::text AS memory_id, "
                    "user_id::text AS user_id, contribution_weight AS weight, "
                    "trigger_action AS action "
                    "FROM attributions WHERE memory_id = ANY(CAST(:ids AS uuid[])) "
                    "ORDER BY memory_id, user_id, created_at DESC, id DESC"
                ),
                {"ids": list(memory_ids)},
            )
        )
        .mappings()
        .all()
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["memory_id"], []).append(dict(row))
    return grouped


def _contributors(
    items: Sequence[dict[str, Any]], names: dict[str, str | None]
) -> list[Contributor]:
    ordered = sorted(items, key=lambda c: (-float(c["weight"]), str(c["user_id"])))
    return [
        Contributor(
            user_id=uuid.UUID(str(c["user_id"])),
            display_name=names.get(str(c["user_id"])),
            weight=float(c["weight"]),
            action=c.get("action"),
        )
        for c in ordered
    ]


async def _graph_agrees_about(
    workspace_id: uuid.UUID, memory_id: uuid.UUID, postgres_row: dict[str, Any] | None
) -> bool:
    """The graph's node for this memory matches PostgreSQL's version, currency and deletion."""
    if postgres_row is None or postgres_row["deleted"]:
        return False
    rows = await run_read(
        MEMORY_NODE_CYPHER, str(workspace_id), {"key": _key(workspace_id, memory_id)}
    )
    return (
        bool(rows)
        and _complete(rows[0])
        and int(rows[0]["version"]) == int(postgres_row["version"])
        and bool(rows[0]["is_current"]) == bool(postgres_row["current_version"])
    )


def _complete(row: dict[str, Any]) -> bool:
    """A node the projector has not filled in yet is a placeholder (seq 0, no version)."""
    return row.get("version") is not None and int(row.get("seq") or 0) > 0


# ── Lineage ──────────────────────────────────────────────────────────────────


async def lineage(
    session: AsyncSession, memory_id: uuid.UUID, workspace_id: uuid.UUID
) -> LineageResponse:
    async def from_graph() -> LineageResponse | None:
        if not await _graph_fresh(session, workspace_id):
            return None
        rows = await run_read(
            LINEAGE_CYPHER,
            str(workspace_id),
            {"key": _key(workspace_id, memory_id), "limit": CHAIN_LIMIT + 1},
        )
        if not rows or len(rows) > CHAIN_LIMIT or not all(_complete(r) for r in rows):
            return None
        ids = [r["id"] for r in rows]
        details = await _memory_rows(session, workspace_id, ids)
        head = details.get(str(memory_id))
        node = next((r for r in rows if r["id"] == str(memory_id)), None)
        if (
            head is None
            or node is None
            or head["deleted"]
            or int(node["version"]) != int(head["version"])
            or bool(node["is_current"]) != bool(head["current_version"])
        ):
            return None  # the graph disagrees with PostgreSQL about this memory
        names = await _display_names(
            session, sorted({c["user_id"] for r in rows for c in r["contributors"]})
        )
        return LineageResponse(
            memory_id=memory_id,
            source="graph",
            versions=[
                LineageVersion(
                    id=uuid.UUID(r["id"]),
                    version=int(r["version"]),
                    is_current=bool(r["is_current"]),
                    created_at=(details.get(r["id"]) or {}).get("created_at"),
                    contributors=_contributors(r["contributors"], names),
                )
                for r in rows
            ],
        )

    graph_answer = await _attempt_graph(session, "lineage", from_graph)
    if graph_answer is not None:
        return graph_answer
    return await _lineage_from_postgres(session, memory_id, workspace_id)


async def _lineage_from_postgres(
    session: AsyncSession, memory_id: uuid.UUID, workspace_id: uuid.UUID
) -> LineageResponse:
    chain = (
        (
            await session.execute(
                text(
                    """
                WITH RECURSIVE chain AS (
                    SELECT id, version, current_version, created_at, parent_memory_id
                    FROM memories
                    WHERE id = CAST(:m AS uuid) AND workspace_id = CAST(:ws AS uuid)
                      AND deleted_at IS NULL
                    UNION
                    SELECT p.id, p.version, p.current_version, p.created_at, p.parent_memory_id
                    FROM memories p JOIN chain c ON p.id = c.parent_memory_id
                    WHERE p.workspace_id = CAST(:ws AS uuid) AND p.deleted_at IS NULL
                )
                SELECT id::text AS id, version, current_version, created_at FROM chain
                ORDER BY version DESC, id::text
                """
                ),
                {"m": str(memory_id), "ws": str(workspace_id)},
            )
        )
        .mappings()
        .all()
    )
    ids = [row["id"] for row in chain]
    contributions = await _latest_contributions(session, ids)
    names = await _display_names(
        session, sorted({c["user_id"] for items in contributions.values() for c in items})
    )
    return LineageResponse(
        memory_id=memory_id,
        source="postgres",
        versions=[
            LineageVersion(
                id=uuid.UUID(row["id"]),
                version=int(row["version"]),
                is_current=bool(row["current_version"]),
                created_at=row["created_at"],
                contributors=_contributors(contributions.get(row["id"], []), names),
            )
            for row in chain
        ],
    )


# ── Related memories ─────────────────────────────────────────────────────────


def _clean_types(types: Sequence[str] | None) -> list[str] | None:
    if not types:
        return None
    return sorted({t for t in types if t in RELATION_TYPES}) or None


async def related(
    session: AsyncSession,
    memory_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    depth: int = 1,
    types: Sequence[str] | None = None,
    limit: int = DEFAULT_RELATED_LIMIT,
) -> RelatedResponse:
    depth = max(1, min(int(depth), MAX_DEPTH))
    limit = max(1, min(int(limit), MAX_RELATED_LIMIT))
    wanted = _clean_types(types)

    async def from_graph() -> RelatedResponse | None:
        if not await _graph_fresh(session, workspace_id):
            return None
        rows = await run_read(
            RELATED_CYPHER[depth],
            str(workspace_id),
            {"key": _key(workspace_id, memory_id), "types": wanted, "limit": limit + 1},
        )
        if not all(_complete(r) for r in rows):
            return None
        truncated = len(rows) > limit
        rows = rows[:limit]
        ids = [str(memory_id), *[r["id"] for r in rows]]
        details = await _memory_rows(session, workspace_id, ids)
        centre = details.get(str(memory_id))
        if not await _graph_agrees_about(workspace_id, memory_id, centre):
            return None
        edges = await run_read(
            RELATED_EDGES_CYPHER, str(workspace_id), {"ids": ids, "types": wanted}
        )
        nodes = [_related_node(str(memory_id), centre, hops=0)]
        for row in rows:
            known = details.get(row["id"])
            if known is None or known["deleted"]:
                return None  # visible in the graph but not to this caller, or gone: do not guess
            if int(row["version"]) != int(known["version"]) or bool(row["is_current"]) != bool(
                known["current_version"]
            ):
                return None
            nodes.append(_related_node(row["id"], known, hops=int(row["hops"])))
        return RelatedResponse(
            memory_id=memory_id,
            source="graph",
            depth_requested=depth,
            depth_served=depth,
            nodes=nodes,
            edges=[
                RelatedEdge(
                    id=uuid.UUID(e["id"]),
                    source=uuid.UUID(e["source"]),
                    target=uuid.UUID(e["target"]),
                    type=e["type"],
                    confidence=e["confidence"],
                )
                for e in edges
            ],
            truncated=truncated,
        )

    graph_answer = await _attempt_graph(session, "related", from_graph)
    if graph_answer is not None:
        return graph_answer
    return await _related_from_postgres(session, memory_id, workspace_id, depth, wanted, limit)


def _related_node(memory_id: str, row: dict[str, Any], *, hops: int) -> RelatedNode:
    return RelatedNode(
        id=uuid.UUID(memory_id),
        version=int(row["version"]),
        is_current=bool(row["current_version"]),
        importance=float(row["importance"] or 0.0),
        hops=hops,
        preview=row.get("preview") or "",
    )


async def _related_from_postgres(
    session: AsyncSession,
    memory_id: uuid.UUID,
    workspace_id: uuid.UUID,
    depth_requested: int,
    types: list[str] | None,
    limit: int,
) -> RelatedResponse:
    """One hop only: a multi-hop walk is what the graph is for."""
    neighbours = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT CASE WHEN source_memory_id = CAST(:m AS uuid) "
                    "THEN target_memory_id ELSE source_memory_id END::text AS id "
                    "FROM memory_relations "
                    "WHERE (source_memory_id = CAST(:m AS uuid) "
                    "OR target_memory_id = CAST(:m AS uuid)) "
                    "AND (CAST(:types AS text[]) IS NULL "
                    "OR relation_type = ANY(CAST(:types AS text[])))"
                ),
                {"m": str(memory_id), "types": types},
            )
        )
        .mappings()
        .all()
    )
    candidate_ids = [row["id"] for row in neighbours if row["id"] != str(memory_id)]
    details = await _memory_rows(session, workspace_id, [str(memory_id), *candidate_ids])
    centre = details.get(str(memory_id))
    visible = sorted(mid for mid in candidate_ids if mid in details and not details[mid]["deleted"])
    truncated = len(visible) > limit
    visible = visible[:limit]
    node_ids = [str(memory_id), *visible]
    edges = (
        (
            await session.execute(
                text(
                    "SELECT id::text AS id, source_memory_id::text AS source, "
                    "target_memory_id::text AS target, relation_type AS type, confidence "
                    "FROM memory_relations "
                    "WHERE source_memory_id = ANY(CAST(:ids AS uuid[])) "
                    "AND target_memory_id = ANY(CAST(:ids AS uuid[])) "
                    "AND (CAST(:types AS text[]) IS NULL "
                    "OR relation_type = ANY(CAST(:types AS text[]))) "
                    "ORDER BY id::text"
                ),
                {"ids": node_ids, "types": types},
            )
        )
        .mappings()
        .all()
    )
    nodes = [_related_node(str(memory_id), centre, hops=0)] if centre else []
    nodes += [_related_node(mid, details[mid], hops=1) for mid in visible]
    return RelatedResponse(
        memory_id=memory_id,
        source="postgres",
        depth_requested=depth_requested,
        depth_served=1,
        nodes=nodes,
        edges=[
            RelatedEdge(
                id=uuid.UUID(e["id"]),
                source=uuid.UUID(e["source"]),
                target=uuid.UUID(e["target"]),
                type=e["type"],
                confidence=None if e["confidence"] is None else float(e["confidence"]),
            )
            for e in edges
        ],
        truncated=truncated,
    )


# ── Who would know ───────────────────────────────────────────────────────────


def score_owners(
    memory_id: str, rows: Sequence[dict[str, Any]], names: dict[str, str | None]
) -> list[OwnerEntry]:
    """Shared by both backends: score = sum(weight * decay^hops) over the memories reached."""
    per_user: dict[str, dict[str, Any]] = {}
    for row in rows:
        weight = float(row["weight"])
        if weight < MIN_CONTRIBUTION_WEIGHT:
            continue
        hops = int(row["hops"])
        path = [str(p) for p in row["path"]]
        entry = per_user.setdefault(
            row["user_id"],
            {"score": 0.0, "direct": None, "hops": hops, "path": path, "best": weight},
        )
        entry["score"] += weight * (OWNER_DECAY**hops)
        if row["memory_id"] == memory_id:
            entry["direct"] = weight
        # The explaining path is the nearest contribution, then the heaviest, then the lowest
        # path: a total order, so both backends and repeated runs choose the same one.
        if (hops, -weight, path) < (entry["hops"], -entry["best"], entry["path"]):
            entry["hops"], entry["path"], entry["best"] = hops, path, weight
    ranked = sorted(per_user.items(), key=lambda item: (-item[1]["score"], item[0]))[:OWNER_LIMIT]
    return [
        OwnerEntry(
            user_id=uuid.UUID(user_id),
            display_name=names.get(user_id),
            score=round(info["score"], 6),
            direct_weight=info["direct"],
            hops=info["hops"],
            path=[uuid.UUID(p) for p in info["path"]],
        )
        for user_id, info in ranked
    ]


async def owners(
    session: AsyncSession, memory_id: uuid.UUID, workspace_id: uuid.UUID
) -> OwnersResponse:
    async def from_graph() -> OwnersResponse | None:
        if not await _graph_fresh(session, workspace_id):
            return None
        head = (await _memory_rows(session, workspace_id, [str(memory_id)])).get(str(memory_id))
        if not await _graph_agrees_about(workspace_id, memory_id, head):
            return None
        rows = await run_read(
            OWNERS_CYPHER,
            str(workspace_id),
            {"key": _key(workspace_id, memory_id), "min_weight": MIN_CONTRIBUTION_WEIGHT},
        )
        names = await _display_names(session, sorted({r["user_id"] for r in rows}))
        return OwnersResponse(
            memory_id=memory_id,
            source="graph",
            depth_served=OWNER_DEPTH,
            owners=score_owners(str(memory_id), rows, names),
        )

    graph_answer = await _attempt_graph(session, "owners", from_graph)
    if graph_answer is not None:
        return graph_answer
    contributions = (await _latest_contributions(session, [str(memory_id)])).get(str(memory_id), [])
    rows = [
        {
            "user_id": c["user_id"],
            "memory_id": str(memory_id),
            "weight": c["weight"],
            "hops": 0,
            "path": [str(memory_id)],
        }
        for c in contributions
    ]
    names = await _display_names(session, sorted({r["user_id"] for r in rows}))
    return OwnersResponse(
        memory_id=memory_id,
        source="postgres",
        depth_served=0,
        owners=score_owners(str(memory_id), rows, names),
    )


# ── Collaboration ────────────────────────────────────────────────────────────


async def collaboration(session: AsyncSession, workspace_id: uuid.UUID) -> CollaborationResponse:
    async def from_graph() -> CollaborationResponse | None:
        if not await _graph_fresh(session, workspace_id):
            return None
        params = {"min_weight": MIN_CONTRIBUTION_WEIGHT}
        nodes = await run_read(
            COLLAB_NODES_CYPHER, str(workspace_id), {**params, "limit": COLLAB_NODE_LIMIT + 1}
        )
        truncated = len(nodes) > COLLAB_NODE_LIMIT
        nodes = nodes[:COLLAB_NODE_LIMIT]
        ids = [n["user_id"] for n in nodes]
        edges = await run_read(
            COLLAB_EDGES_CYPHER,
            str(workspace_id),
            {**params, "ids": ids, "limit": COLLAB_EDGE_LIMIT + 1},
        )
        truncated = truncated or len(edges) > COLLAB_EDGE_LIMIT
        return await _collaboration_response(
            session, "graph", nodes, edges[:COLLAB_EDGE_LIMIT], truncated
        )

    graph_answer = await _attempt_graph(session, "collaboration", from_graph)
    if graph_answer is not None:
        return graph_answer
    return await _collaboration_from_postgres(session, workspace_id)


async def _collaboration_response(
    session: AsyncSession,
    source: str,
    nodes: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
    truncated: bool,
) -> CollaborationResponse:
    names = await _display_names(session, [n["user_id"] for n in nodes])
    return CollaborationResponse(
        source=source,  # type: ignore[arg-type]
        nodes=[
            CollaborationNode(
                user_id=uuid.UUID(n["user_id"]),
                display_name=names.get(n["user_id"]),
                memory_count=int(n["memory_count"]),
            )
            for n in nodes
        ],
        edges=[
            CollaborationEdge(
                source=uuid.UUID(e["source"]),
                target=uuid.UUID(e["target"]),
                shared_memories=int(e["shared_memories"]),
            )
            for e in edges
        ],
        truncated=truncated,
    )


_LATEST_STRONG = """
WITH latest AS (
    SELECT DISTINCT ON (a.memory_id, a.user_id)
           a.memory_id, a.user_id, a.contribution_weight AS weight
    FROM attributions a
    JOIN memories m ON m.id = a.memory_id
    WHERE m.workspace_id = CAST(:ws AS uuid) AND m.current_version AND m.deleted_at IS NULL
    ORDER BY a.memory_id, a.user_id, a.created_at DESC, a.id DESC
), strong AS (
    SELECT memory_id, user_id FROM latest WHERE weight >= :min_weight
)
"""


async def _collaboration_from_postgres(
    session: AsyncSession, workspace_id: uuid.UUID
) -> CollaborationResponse:
    params = {"ws": str(workspace_id), "min_weight": MIN_CONTRIBUTION_WEIGHT}
    nodes = (
        (
            await session.execute(
                text(
                    _LATEST_STRONG
                    + "SELECT user_id::text AS user_id, count(*) AS memory_count FROM strong "
                    "GROUP BY user_id ORDER BY memory_count DESC, user_id::text LIMIT :limit"
                ),
                {**params, "limit": COLLAB_NODE_LIMIT + 1},
            )
        )
        .mappings()
        .all()
    )
    truncated = len(nodes) > COLLAB_NODE_LIMIT
    nodes = [dict(n) for n in nodes[:COLLAB_NODE_LIMIT]]
    edges = (
        (
            await session.execute(
                text(
                    _LATEST_STRONG + "SELECT a.user_id::text AS source, b.user_id::text AS target, "
                    "count(*) AS shared_memories FROM strong a JOIN strong b "
                    "ON a.memory_id = b.memory_id AND a.user_id::text < b.user_id::text "
                    "WHERE a.user_id = ANY(CAST(:ids AS uuid[])) "
                    "AND b.user_id = ANY(CAST(:ids AS uuid[])) "
                    "GROUP BY a.user_id, b.user_id "
                    "ORDER BY shared_memories DESC, source, target LIMIT :limit"
                ),
                {**params, "ids": [n["user_id"] for n in nodes], "limit": COLLAB_EDGE_LIMIT + 1},
            )
        )
        .mappings()
        .all()
    )
    truncated = truncated or len(edges) > COLLAB_EDGE_LIMIT
    return await _collaboration_response(
        session, "postgres", nodes, [dict(e) for e in edges[:COLLAB_EDGE_LIMIT]], truncated
    )
