"""The graph's answers against PostgreSQL's, for the same data, through the real pipeline.

PostgreSQL stays the source of truth and every graph read falls back to it, so each graph answer
must equal its PostgreSQL twin wherever the two can both answer. Where the graph knows more (related
memories beyond one hop, contributors reached through relations) the test pins that difference by
hand instead. Also: a graph that disagrees with PostgreSQL is not trusted, and another workspace's
memory stays invisible on the graph path.

Needs everything the end-to-end test needs: the disposable PostgreSQL at migration 20261029_0016
and the graph test stack (see ``test_graph_pipeline_end_to_end``). Skipped when any is missing.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sourcemind.core import graph
from sourcemind.core.config import get_settings
from sourcemind.core.graph import run_read
from sourcemind.events import projector, relay
from sourcemind.events.envelope import EVENT_BACKFILL_COMPLETE, EVENT_BACKFILL_STARTED
from sourcemind.models.attribution import Attribution
from sourcemind.models.memory import Memory
from sourcemind.services.graph import reads
from sourcemind.services.graph.outbox import emit_memory_states, emit_workspace_marker
from tests.integration.test_graph_outbox_real_db import (  # noqa: F401  (fixtures)
    _as_owner,
    _flag,
    owner_engine,
    relay_engine,
)
from tests.integration.test_graph_projection_real_services import (  # noqa: F401  (fixtures)
    _wait_for,
    stack,
)
from tests.integration.test_graph_reads_real_db import _scenario
from tests.integration.test_security_foundation_real_db import _seed_security_tenants

pytestmark = pytest.mark.asyncio


def _graph_on():
    return patch.object(
        reads, "get_settings", return_value=SimpleNamespace(ff_neo4j_attribution=True)
    )


def _graph_off():
    return patch.object(reads, "graph_usable", return_value=False)


@pytest_asyncio.fixture
async def projected(
    stack,  # noqa: F811
    supabase_engine: AsyncEngine,
    owner_engine: AsyncEngine,  # noqa: F811
    relay_engine: AsyncEngine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    """Seed PostgreSQL, run the relay and projector until the graph has caught up, hand back ids."""
    monkeypatch.setenv("GRAPH_RELAY_DATABASE_URL", os.environ["TEST_GRAPH_RELAY_DATABASE_URL"])
    monkeypatch.setenv("GRAPH_RELAY_SETTLE_SECONDS", "0")
    monkeypatch.setenv("GRAPH_RELAY_POLL_SECONDS", "0.2")
    get_settings.cache_clear()

    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        v2, c = await _scenario(session, data)
        isolated = (
            Memory(  # no relations: the only memory whose graph and PostgreSQL owners coincide
                workspace_id=data.target_workspace_id,
                content="an isolated memory",
                content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            )
        )
        session.add(isolated)
        await session.flush()
        session.add(
            Attribution(
                memory_id=isolated.id,
                user_id=data.owner_id,
                contribution_weight=0.6,
                trigger_action="create",
            )
        )
        await session.flush()
        # Like the real backfill: bracket the full state with the two markers, because a
        # workspace is trusted only once a completed backfill has been applied.
        started = await emit_workspace_marker(
            session, data.target_workspace_id, EVENT_BACKFILL_STARTED, {}
        )
        with _flag(True):
            await emit_memory_states(
                session,
                [v2.id, c.id, isolated.id, data.target_memory_id],
                reason="edit",
            )
        await emit_workspace_marker(
            session,
            data.target_workspace_id,
            EVENT_BACKFILL_COMPLETE,
            {"started_seq": started},
        )
        await session.commit()
    finally:
        await session.close()

    ws = str(data.target_workspace_id)
    wanted = {str(v2.id), str(c.id), str(isolated.id), str(data.target_memory_id)}

    async def caught_up() -> bool:
        rows = await run_read(
            "MATCH (m:Memory {workspace_id: $workspace_id}) WHERE m.seq > 0 RETURN m.id AS id", ws
        )
        if not wanted <= {r["id"] for r in rows}:
            return False
        mark = await run_read(
            "MATCH (w:Workspace {key: $workspace_id}) RETURN w.applied_seq AS n", ws
        )
        async with relay_engine.connect() as conn:
            published = (
                await conn.execute(
                    text(
                        "SELECT published_seq FROM graph_publication_cursor "
                        "WHERE workspace_id = CAST(:ws AS uuid)"
                    ),
                    {"ws": ws},
                )
            ).scalar()
        return bool(mark) and published is not None and mark[0]["n"] >= published

    stop = asyncio.Event()
    await graph.close_graph()  # the projector owns its driver, as in production
    relay_task = asyncio.create_task(relay.run(stop))
    projector_task = asyncio.create_task(projector.run(stop))
    try:
        await _wait_for(caught_up, timeout=90, what="the graph to catch up with PostgreSQL")
    finally:
        stop.set()
        assert await asyncio.wait_for(relay_task, timeout=30) == 0
        assert await asyncio.wait_for(projector_task, timeout=30) == 0
        await graph.init_graph()
    return SimpleNamespace(data=data, v2=v2, c=c, isolated=isolated)


async def _session(supabase_engine, data):
    return await _as_owner(supabase_engine, data)


def _same(a, b) -> bool:
    return a.model_dump(exclude={"source"}) == b.model_dump(exclude={"source"})


async def test_lineage_is_identical_from_either_backend(projected, supabase_engine) -> None:
    session = await _session(supabase_engine, projected.data)
    try:
        ws = projected.data.target_workspace_id
        with _graph_on():
            from_graph = await reads.lineage(session, projected.v2.id, ws)
        with _graph_off():
            from_pg = await reads.lineage(session, projected.v2.id, ws)
    finally:
        await session.rollback()
        await session.close()
    assert from_graph.source == "graph" and from_pg.source == "postgres"
    assert [v.version for v in from_graph.versions] == [2, 1]
    assert _same(from_graph, from_pg)


async def test_related_is_identical_at_one_hop_and_reaches_further_on_the_graph(
    projected, supabase_engine
) -> None:
    session = await _session(supabase_engine, projected.data)
    try:
        ws, v1 = projected.data.target_workspace_id, projected.data.target_memory_id
        with _graph_on():
            graph_one = await reads.related(session, projected.v2.id, ws, depth=1)
            graph_types = await reads.related(
                session, projected.v2.id, ws, depth=1, types=["updates"]
            )
            from_v1 = await reads.related(session, v1, ws, depth=2)  # v1 - v2 - c
        with _graph_off():
            pg_one = await reads.related(session, projected.v2.id, ws, depth=1)
            pg_types = await reads.related(session, projected.v2.id, ws, depth=1, types=["updates"])
            pg_from_v1 = await reads.related(session, v1, ws, depth=2)
    finally:
        await session.rollback()
        await session.close()

    assert graph_one.source == "graph" and pg_one.source == "postgres"
    assert _same(graph_one, pg_one)
    assert _same(graph_types, pg_types) and {e.type for e in graph_types.edges} == {"updates"}

    # What only the graph can do: two hops, with the hop count reported.
    assert from_v1.source == "graph" and from_v1.depth_served == 2
    assert {str(n.id): n.hops for n in from_v1.nodes} == {
        str(v1): 0,
        str(projected.v2.id): 1,
        str(projected.c.id): 2,
    }
    assert pg_from_v1.depth_served == 1 and str(projected.c.id) not in {
        str(n.id) for n in pg_from_v1.nodes
    }


async def test_owners_match_where_both_can_answer_and_the_graph_adds_reachable_experts(
    projected, supabase_engine
) -> None:
    session = await _session(supabase_engine, projected.data)
    try:
        ws, data = projected.data.target_workspace_id, projected.data
        with _graph_on():
            isolated_graph = await reads.owners(session, projected.isolated.id, ws)
            v2_graph = await reads.owners(session, projected.v2.id, ws)
        with _graph_off():
            isolated_pg = await reads.owners(session, projected.isolated.id, ws)
            v2_pg = await reads.owners(session, projected.v2.id, ws)
    finally:
        await session.rollback()
        await session.close()

    # A memory with no relations: nothing for the graph to add, so the answers coincide.
    assert isolated_graph.source == "graph" and isolated_pg.source == "postgres"
    assert isolated_graph.owners == isolated_pg.owners
    assert [o.direct_weight for o in isolated_graph.owners] == [0.6]

    # Memory v2 is updated by C, which only the member touched. Through that relation the member's
    # score is 0.5 (direct) + 0.9 * 0.5 (one hop), above the owner's 0.75.
    assert v2_graph.depth_served == 2 and v2_pg.depth_served == 0
    graph_scores = {str(o.user_id): o for o in v2_graph.owners}
    assert [str(o.user_id) for o in v2_graph.owners] == [str(data.member_id), str(data.owner_id)]
    assert graph_scores[str(data.member_id)].score == pytest.approx(0.5 + 0.9 * 0.5)
    assert graph_scores[str(data.member_id)].direct_weight == 0.5
    assert graph_scores[str(data.owner_id)].score == pytest.approx(0.75)
    assert [str(o.user_id) for o in v2_pg.owners] == [str(data.owner_id), str(data.member_id)]


async def test_collaboration_is_identical_from_either_backend(projected, supabase_engine) -> None:
    session = await _session(supabase_engine, projected.data)
    try:
        ws = projected.data.target_workspace_id
        with _graph_on():
            from_graph = await reads.collaboration(session, ws)
        with _graph_off():
            from_pg = await reads.collaboration(session, ws)
    finally:
        await session.rollback()
        await session.close()
    assert from_graph.source == "graph" and from_pg.source == "postgres"
    assert _same(from_graph, from_pg)
    assert {n.memory_count for n in from_graph.nodes} == {
        2,
    }  # owner: v2 + isolated; member: v2 + c
    assert [e.shared_memories for e in from_graph.edges] == [1]


async def test_a_graph_that_disagrees_with_postgres_is_not_trusted(
    projected, supabase_engine
) -> None:
    """Edit v2 in PostgreSQL only: the graph still says version 2, so it must not answer."""
    session = await _session(supabase_engine, projected.data)
    try:
        ws = projected.data.target_workspace_id
        await session.execute(
            text("UPDATE memories SET version = 7 WHERE id = CAST(:id AS uuid)"),
            {"id": str(projected.v2.id)},
        )
        with _graph_on():
            lineage = await reads.lineage(session, projected.v2.id, ws)
            owners = await reads.owners(session, projected.v2.id, ws)
            related = await reads.related(session, projected.v2.id, ws)
    finally:
        await session.rollback()
        await session.close()
    assert lineage.source == owners.source == related.source == "postgres"
    assert lineage.versions[0].version == 7


async def test_another_workspaces_memory_is_invisible_on_the_graph_path_too(
    projected, supabase_engine
) -> None:
    session = await _session(supabase_engine, projected.data)
    try:
        ws, other = projected.data.target_workspace_id, projected.data.other_memory_id
        with _graph_on():
            assert (await reads.lineage(session, other, ws)).versions == []
            assert (await reads.related(session, other, ws)).nodes == []
            assert (await reads.owners(session, other, ws)).owners == []
    finally:
        await session.rollback()
        await session.close()
