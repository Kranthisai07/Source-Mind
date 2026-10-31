"""The read seam: when the graph may answer, and what happens when it may not."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from sourcemind.services.graph import reads
from tests.unit.events.fakes import RoutedSession, Rows

WS = uuid.uuid4()
MEM = uuid.uuid4()
PARENT = uuid.uuid4()
USER_A, USER_B = str(uuid.uuid4()), str(uuid.uuid4())
NOW = datetime(2026, 10, 29, tzinfo=UTC)


def _graph(on: bool = True):
    return patch.object(reads, "graph_usable", return_value=on)


def _watermark(applied, baseline=1):
    return [{"seq": applied, "baseline_seq": baseline}] if applied is not None else []


def _pg_session(*, cursor: int | None = 10, memories=None, contributions=()):
    """A session answering the PostgreSQL side of lineage."""
    memories = (
        memories
        if memories is not None
        else [
            {
                "id": str(MEM),
                "version": 2,
                "current_version": True,
                "deleted": False,
                "importance": 0.5,
                "created_at": NOW,
                "preview": "head",
            },
            {
                "id": str(PARENT),
                "version": 1,
                "current_version": False,
                "deleted": False,
                "importance": 0.4,
                "created_at": NOW,
                "preview": "root",
            },
        ]
    )
    return RoutedSession(
        {
            "FROM graph_publication_cursor": Rows(
                [] if cursor is None else [{"published_seq": cursor}]
            ),
            "WITH RECURSIVE chain": Rows(
                [
                    {"id": str(MEM), "version": 2, "current_version": True, "created_at": NOW},
                    {"id": str(PARENT), "version": 1, "current_version": False, "created_at": NOW},
                ]
            ),
            "FROM attributions": Rows(list(contributions)),
            "FROM users": Rows([{"id": USER_A, "display_name": "Ada"}]),
            "FROM memories WHERE workspace_id": lambda p: Rows(
                [m for m in memories if m["id"] in p["ids"]]
            ),
        }
    )


def _graph_chain(version_head=2, current_head=True, seq=5):
    return [
        {
            "id": str(MEM),
            "version": version_head,
            "is_current": current_head,
            "seq": seq,
            "contributors": [{"user_id": USER_A, "weight": 0.75, "action": "edit"}],
        },
        {"id": str(PARENT), "version": 1, "is_current": False, "seq": seq, "contributors": []},
    ]


def _read_router(chain, watermark=10):
    async def fake(query, workspace_id, params=None):
        if query is reads.WORKSPACE_WATERMARK:
            return _watermark(watermark)
        if query is reads.LINEAGE_CYPHER:
            return chain
        raise AssertionError("unexpected graph query")

    return fake


# ── lineage: when the graph may answer ───────────────────────────────────────


@pytest.mark.unit
async def test_a_fresh_agreeing_graph_answers_and_says_so() -> None:
    session = _pg_session()
    with _graph(), patch.object(reads, "run_read", _read_router(_graph_chain())):
        out = await reads.lineage(session, MEM, WS)
    assert out.source == "graph"
    assert [v.version for v in out.versions] == [2, 1]
    assert out.versions[0].contributors[0].display_name == "Ada"
    assert out.versions[0].created_at == NOW  # enriched from PostgreSQL, not stored in the graph


@pytest.mark.unit
async def test_graph_off_means_postgres_and_the_graph_is_never_touched() -> None:
    session = _pg_session()
    graph_call = AsyncMock()
    with _graph(False), patch.object(reads, "run_read", graph_call):
        out = await reads.lineage(session, MEM, WS)
    assert out.source == "postgres" and graph_call.await_count == 0
    assert getattr(session, "savepoints", 0) == 0


@pytest.mark.unit
async def test_a_graph_error_is_swallowed_and_postgres_answers_inside_a_savepoint() -> None:
    session = _pg_session()
    with _graph(), patch.object(reads, "run_read", AsyncMock(side_effect=RuntimeError("down"))):
        out = await reads.lineage(session, MEM, WS)
    assert out.source == "postgres"
    assert (
        session.savepoints == 1 and session.rolled_back == 1
    )  # the failed attempt was rolled back


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cursor", "applied", "expect_graph"),
    [
        (None, 99, False),  # nothing was ever published for this workspace
        (10, None, False),  # the projector has applied nothing
        (10, 9, False),  # the projector is behind the relay
        (10, 10, True),
        (10, 11, True),
    ],
)
async def test_freshness_is_applied_seq_reaching_the_published_cursor(
    cursor, applied, expect_graph
) -> None:
    session = _pg_session(cursor=cursor)
    with _graph(), patch.object(reads, "run_read", _read_router(_graph_chain(), applied)):
        out = await reads.lineage(session, MEM, WS)
    assert (out.source == "graph") is expect_graph


@pytest.mark.unit
@pytest.mark.parametrize(
    "chain",
    [
        _graph_chain(version_head=3),  # postgres says version 2
        _graph_chain(current_head=False),  # postgres says current
        _graph_chain(seq=0),  # placeholder node the projector never filled in
        [],  # the graph does not know the memory
    ],
)
async def test_a_graph_that_disagrees_or_is_incomplete_is_not_trusted(chain) -> None:
    session = _pg_session()
    with _graph(), patch.object(reads, "run_read", _read_router(chain)):
        out = await reads.lineage(session, MEM, WS)
    assert out.source == "postgres"


@pytest.mark.unit
async def test_a_memory_deleted_in_postgres_is_never_served_from_the_graph() -> None:
    memories = [
        {
            "id": str(MEM),
            "version": 2,
            "current_version": True,
            "deleted": True,
            "importance": 0,
            "created_at": NOW,
            "preview": "",
        }
    ]
    session = _pg_session(memories=memories)
    with _graph(), patch.object(reads, "run_read", _read_router(_graph_chain())):
        out = await reads.lineage(session, MEM, WS)
    assert out.source == "postgres"


@pytest.mark.unit
async def test_the_postgres_fallback_has_the_same_shape_as_the_graph_answer() -> None:
    contributions = [{"memory_id": str(MEM), "user_id": USER_A, "weight": 0.75, "action": "edit"}]
    graph_session = _pg_session(contributions=contributions)
    with _graph(), patch.object(reads, "run_read", _read_router(_graph_chain())):
        from_graph = await reads.lineage(graph_session, MEM, WS)
    with _graph(False):
        from_pg = await reads.lineage(_pg_session(contributions=contributions), MEM, WS)
    assert from_graph.model_dump(exclude={"source"}) == from_pg.model_dump(exclude={"source"})


# ── related ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_every_cypher_statement_is_scoped_and_depth_selects_among_constants() -> None:
    statements = [
        reads.LINEAGE_CYPHER,
        reads.RELATED_EDGES_CYPHER,
        reads.OWNERS_CYPHER,
        reads.COLLAB_NODES_CYPHER,
        reads.COLLAB_EDGES_CYPHER,
        reads.WORKSPACE_WATERMARK,
        *reads.RELATED_CYPHER.values(),
    ]
    assert all("$workspace_id" in s for s in statements)
    assert sorted(reads.RELATED_CYPHER) == [1, 2, 3]
    for depth, statement in reads.RELATED_CYPHER.items():
        assert f"*1..{depth}" in statement
    with pytest.raises(AssertionError):
        reads._related_cypher(4)  # no other bound can ever be substituted


@pytest.mark.unit
async def test_depth_and_limit_are_clamped_and_unknown_types_dropped() -> None:
    seen = {}

    async def fake(query, workspace_id, params=None):
        if query is reads.WORKSPACE_WATERMARK:
            return _watermark(10)
        if query in reads.RELATED_CYPHER.values():
            seen["query"], seen["params"] = query, params
        return []

    session = _pg_session()
    with _graph(), patch.object(reads, "run_read", fake):
        await reads.related(
            session, MEM, WS, depth=99, types=["extends", "'; DROP", "updates"], limit=10_000
        )
    assert seen["query"] is reads.RELATED_CYPHER[3]
    assert seen["params"]["limit"] == reads.MAX_RELATED_LIMIT + 1
    assert seen["params"]["types"] == ["extends", "updates"]


@pytest.mark.unit
async def test_the_postgres_related_fallback_serves_one_hop_and_says_so() -> None:
    other = str(uuid.uuid4())
    session = RoutedSession(
        {
            "SELECT DISTINCT CASE": Rows([{"id": other}, {"id": str(MEM)}]),
            "FROM memories WHERE workspace_id": lambda p: Rows(
                [
                    {
                        "id": str(MEM),
                        "version": 1,
                        "current_version": True,
                        "deleted": False,
                        "importance": 0.1,
                        "created_at": NOW,
                        "preview": "centre",
                    },
                    {
                        "id": other,
                        "version": 3,
                        "current_version": True,
                        "deleted": False,
                        "importance": 0.2,
                        "created_at": NOW,
                        "preview": "neighbour",
                    },
                ]
            ),
            "FROM memory_relations WHERE source_memory_id = ANY": Rows([]),
        }
    )
    with _graph(False):
        out = await reads.related(session, MEM, WS, depth=3)
    assert out.source == "postgres" and out.depth_requested == 3 and out.depth_served == 1
    assert [(str(n.id), n.hops) for n in out.nodes] == [(str(MEM), 0), (other, 1)]


# ── owners ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_owner_scoring_decays_with_distance_and_ignores_noise() -> None:
    m = str(MEM)
    near, far = str(uuid.uuid4()), str(uuid.uuid4())
    rows = [
        {"user_id": USER_A, "memory_id": m, "weight": 0.6, "hops": 0, "path": [m]},
        {"user_id": USER_A, "memory_id": near, "weight": 0.4, "hops": 1, "path": [m, near]},
        {"user_id": USER_B, "memory_id": far, "weight": 0.9, "hops": 2, "path": [m, near, far]},
        {"user_id": str(uuid.uuid4()), "memory_id": m, "weight": 0.01, "hops": 0, "path": [m]},
    ]
    out = reads.score_owners(m, rows, {USER_A: "Ada"})
    assert [str(o.user_id) for o in out] == [USER_A, USER_B]  # the 0.01 contribution is noise
    assert out[0].score == pytest.approx(0.6 + 0.4 * 0.5) and out[0].direct_weight == 0.6
    assert out[0].hops == 0 and out[0].display_name == "Ada"
    assert out[1].score == pytest.approx(0.9 * 0.25) and out[1].direct_weight is None
    assert [str(p) for p in out[1].path] == [m, near, far]


@pytest.mark.unit
def test_owner_ties_choose_the_same_path_whatever_the_row_order() -> None:
    m, a, b = str(MEM), str(uuid.uuid4()), str(uuid.uuid4())
    rows = [
        {"user_id": USER_A, "memory_id": a, "weight": 0.5, "hops": 1, "path": [m, a]},
        {"user_id": USER_A, "memory_id": b, "weight": 0.5, "hops": 1, "path": [m, b]},
    ]
    first = reads.score_owners(m, rows, {})
    second = reads.score_owners(m, list(reversed(rows)), {})
    assert first[0].path == second[0].path


@pytest.mark.unit
async def test_the_postgres_owners_fallback_is_direct_contributors_only() -> None:
    session = RoutedSession(
        {
            "FROM attributions": Rows(
                [{"memory_id": str(MEM), "user_id": USER_A, "weight": 0.7, "action": "edit"}]
            ),
            "FROM users": Rows([{"id": USER_A, "display_name": "Ada"}]),
        }
    )
    with _graph(False):
        out = await reads.owners(session, MEM, WS)
    assert out.source == "postgres" and out.depth_served == 0
    assert out.owners[0].hops == 0 and out.owners[0].direct_weight == 0.7


# ── collaboration ────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_collaboration_falls_back_when_the_graph_is_behind() -> None:
    session = RoutedSession(
        {
            "FROM graph_publication_cursor": Rows([{"published_seq": 10}]),
            "WITH latest AS": Rows([]),
            "FROM users": Rows([]),
        }
    )

    async def fake(query, workspace_id, params=None):
        assert query is reads.WORKSPACE_WATERMARK
        return _watermark(3)

    with _graph(), patch.object(reads, "run_read", fake):
        out = await reads.collaboration(session, WS)
    assert out.source == "postgres" and out.nodes == [] and out.edges == []


@pytest.mark.unit
async def test_collaboration_is_capped_and_reports_truncation() -> None:
    users = [str(uuid.uuid4()) for _ in range(reads.COLLAB_NODE_LIMIT + 1)]

    async def fake(query, workspace_id, params=None):
        if query is reads.WORKSPACE_WATERMARK:
            return _watermark(10)
        if query is reads.COLLAB_NODES_CYPHER:
            assert params["limit"] == reads.COLLAB_NODE_LIMIT + 1
            return [{"user_id": u, "memory_count": 1} for u in users]
        assert query is reads.COLLAB_EDGES_CYPHER
        assert len(params["ids"]) == reads.COLLAB_NODE_LIMIT
        return []

    session = RoutedSession(
        {
            "FROM graph_publication_cursor": Rows([{"published_seq": 10}]),
            "FROM users": Rows([]),
        }
    )
    with _graph(), patch.object(reads, "run_read", fake):
        out = await reads.collaboration(session, WS)
    assert out.source == "graph" and out.truncated is True
    assert len(out.nodes) == reads.COLLAB_NODE_LIMIT


@pytest.mark.unit
def test_responses_carry_no_memory_content_fields() -> None:
    from sourcemind.schemas import graph as schemas

    for model in (
        schemas.LineageVersion,
        schemas.RelatedNode,
        schemas.OwnerEntry,
        schemas.CollaborationNode,
    ):
        assert "content" not in model.model_fields and "email" not in model.model_fields
    sql = " ".join(
        str(call)
        for call in (reads._memory_rows.__code__.co_consts)  # noqa: SLF001
    )
    assert "left(" in sql  # previews are bounded to 120 characters in the query itself


@pytest.mark.unit
@pytest.mark.parametrize("graph_version", [1, 3])
async def test_related_does_not_trust_a_graph_whose_node_for_the_memory_is_stale(
    graph_version,
) -> None:
    """Regression: the centre memory was never compared with PostgreSQL, so a stale graph won."""

    async def fake(query, workspace_id, params=None):
        if query is reads.WORKSPACE_WATERMARK:
            return _watermark(10)
        if query is reads.MEMORY_NODE_CYPHER:
            return [{"version": graph_version, "is_current": True, "seq": 5}]
        return []

    session = _pg_session()  # PostgreSQL says version 2
    with _graph(), patch.object(reads, "run_read", fake):
        out = await reads.related(session, MEM, WS)
    assert out.source == "postgres"


@pytest.mark.unit
async def test_related_trusts_a_graph_whose_node_agrees() -> None:
    async def fake(query, workspace_id, params=None):
        if query is reads.WORKSPACE_WATERMARK:
            return _watermark(10)
        if query is reads.MEMORY_NODE_CYPHER:
            return [{"version": 2, "is_current": True, "seq": 5}]
        return []

    with _graph(), patch.object(reads, "run_read", fake):
        out = await reads.related(_pg_session(), MEM, WS)
    assert out.source == "graph" and [str(n.id) for n in out.nodes] == [str(MEM)]


@pytest.mark.unit
async def test_a_workspace_with_a_dead_letter_gap_is_never_fresh_even_when_caught_up() -> None:
    async def run_with(rows):
        async def fake(query, workspace_id, params=None):
            assert query is reads.WORKSPACE_WATERMARK
            return rows

        with patch.object(reads, "run_read", fake):
            return await reads._graph_fresh(_pg_session(cursor=10), WS)

    # Caught up (applied 12 >= published 10), baselined, no gap: trusted.
    assert await run_with([{"seq": 12, "gap_seq": None, "baseline_seq": 5}]) is True
    # Same watermark, but an event was dead-lettered and not repaired: not trusted.
    assert await run_with([{"seq": 12, "gap_seq": 7, "baseline_seq": 5}]) is False
    # A row from before the gap field existed behaves as "no gap".
    assert await run_with([{"seq": 12, "baseline_seq": 5}]) is True


@pytest.mark.unit
async def test_caught_up_without_a_completed_backfill_is_not_trusted() -> None:
    """Codex, #46: events are one memory each, so a workspace that already had data when the
    pipeline was switched on looks caught up after its first new event while missing the rest."""

    async def run_with(rows):
        async def fake(query, workspace_id, params=None):
            return rows

        with patch.object(reads, "run_read", fake):
            return await reads._graph_fresh(_pg_session(cursor=10), WS)

    assert await run_with([{"seq": 12, "gap_seq": None, "baseline_seq": None}]) is False
    assert await run_with([{"seq": 12, "gap_seq": None}]) is False  # key absent: same thing
    assert await run_with([{"seq": 12, "gap_seq": None, "baseline_seq": 5}]) is True
