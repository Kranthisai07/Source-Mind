"""A change committed in PostgreSQL reaches Neo4j through the whole pipeline, for real.

PostgreSQL ``graph_outbox`` -> relay -> Redpanda -> projector -> Neo4j, with nothing faked.
Needs everything the two neighbouring modules need: the disposable PostgreSQL with migration
20261029_0015 (``MIGRATION_DATABASE_URL``, ``TEST_GRAPH_RELAY_DATABASE_URL``) and the graph test
stack (``GRAPH_TEST_NEO4J_PASSWORD``). Skipped when any of them is missing.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sourcemind.core import graph
from sourcemind.core.graph import run_read
from sourcemind.events import projector, relay, status
from sourcemind.services.graph.outbox import emit_memory_states
from tests.integration.test_graph_outbox_real_db import (  # noqa: F401  (fixtures)
    _as_owner,
    _flag,
    _new_version,
    _rows,
    owner_engine,
    relay_engine,
)
from tests.integration.test_graph_projection_real_services import (  # noqa: F401  (fixtures)
    _memory,
    _wait_for,
    stack,
)
from tests.integration.test_security_foundation_real_db import _seed_security_tenants

pytestmark = pytest.mark.asyncio


async def test_a_committed_change_reaches_the_graph_through_the_whole_pipeline(
    stack,  # noqa: F811
    supabase_engine: AsyncEngine,
    owner_engine: AsyncEngine,  # noqa: F811
    relay_engine: AsyncEngine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sourcemind.core.config import get_settings

    monkeypatch.setenv("GRAPH_RELAY_DATABASE_URL", os.environ["TEST_GRAPH_RELAY_DATABASE_URL"])
    monkeypatch.setenv("GRAPH_RELAY_SETTLE_SECONDS", "0")
    monkeypatch.setenv("GRAPH_RELAY_POLL_SECONDS", "0.2")
    get_settings.cache_clear()

    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        memory = await _new_version(session, data, label="end-to-end")
        with _flag(True):
            await emit_memory_states(session, [memory.id, data.target_memory_id], reason="edit")
        await session.commit()
    finally:
        await session.close()

    ws, mem, parent = (
        str(data.target_workspace_id),
        str(memory.id),
        str(data.target_memory_id),
    )

    async def arrived() -> bool:
        node = await _memory(ws, mem)
        if node.get("version") != 2:
            return False
        rows = await run_read(
            "MATCH (u:User)-[c:CONTRIBUTED]->(m:Memory {key: $m, workspace_id: $workspace_id}) "
            "RETURN u.id AS user, c.weight AS weight",
            ws,
            {"m": f"{ws}:{mem}"},
        )
        return bool(rows)

    stop = asyncio.Event()
    await graph.close_graph()  # the projector owns its driver, as in production
    relay_task = asyncio.create_task(relay.run(stop))
    projector_task = asyncio.create_task(projector.run(stop))
    try:
        await _wait_for(arrived, timeout=90, what="the committed edit to appear in Neo4j")
    finally:
        stop.set()
        assert await asyncio.wait_for(relay_task, timeout=30) == 0
        assert await asyncio.wait_for(projector_task, timeout=30) == 0
        await graph.init_graph()

    node = await _memory(ws, mem)
    assert node["parent_id"] == parent and node["current"] is True
    contributions = await run_read(
        "MATCH (u:User)-[c:CONTRIBUTED]->(m:Memory {key: $m, workspace_id: $workspace_id}) "
        "RETURN u.id AS user, c.weight AS weight, c.action AS action",
        ws,
        {"m": f"{ws}:{mem}"},
    )
    assert contributions == [{"user": str(data.owner_id), "weight": 0.75, "action": "edit"}]
    edges = await run_read(
        "MATCH (:Memory {key: $m, workspace_id: $workspace_id})-[r:RELATES]->(t:Memory) "
        "RETURN t.id AS target, r.type AS type",
        ws,
        {"m": f"{ws}:{mem}"},
    )
    assert edges == [{"target": parent, "type": "extends"}]
    chain = await run_read(
        "MATCH (:Memory {key: $m, workspace_id: $workspace_id})-[:PARENT]->(p:Memory) "
        "RETURN p.id AS parent",
        ws,
        {"m": f"{ws}:{mem}"},
    )
    assert chain == [{"parent": parent}]

    # The relay marked what it published, and the projector advanced the workspace watermark.
    published = await _rows(owner_engine, memory.id)
    assert published and all(row["published_at"] is not None for row in published)
    watermark = await run_read(
        "MATCH (w:Workspace {key: $workspace_id}) RETURN w.applied_seq AS seq", ws
    )
    assert watermark[0]["seq"] >= max(row["seq"] for row in published)

    # Nothing about this workspace leaked into another one's graph.
    other = str(data.other_workspace_id)
    leaked = await run_read(
        "MATCH (n:Memory {workspace_id: $workspace_id}) RETURN count(n) AS n", other
    )
    assert leaked[0]["n"] == 0
    async with owner_engine.connect() as conn:
        pending = (
            await conn.execute(text("SELECT count(*) FROM graph_outbox WHERE published_at IS NULL"))
        ).scalar()
    assert pending == 0


async def test_a_dead_lettered_event_gaps_the_workspace_until_a_backfill_completes(
    stack,  # noqa: F811
    supabase_engine: AsyncEngine,
    owner_engine: AsyncEngine,  # noqa: F811
    relay_engine: AsyncEngine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-062 end to end: poison event -> gap -> reads distrust the graph -> backfill -> trusted."""
    from sourcemind.core.config import get_settings
    from sourcemind.events import backfill
    from sourcemind.services.graph import reads

    monkeypatch.setenv("GRAPH_RELAY_DATABASE_URL", os.environ["TEST_GRAPH_RELAY_DATABASE_URL"])
    monkeypatch.setenv("GRAPH_RELAY_SETTLE_SECONDS", "0")
    monkeypatch.setenv("GRAPH_RELAY_POLL_SECONDS", "0.2")
    get_settings.cache_clear()

    data = await _seed_security_tenants(supabase_engine)
    ws = str(data.target_workspace_id)
    session = await _as_owner(supabase_engine, data)
    try:
        memory = await _new_version(session, data, label="gap")
        with _flag(True):
            await emit_memory_states(session, [memory.id], reason="edit")
        await session.commit()
    finally:
        await session.close()
    # A poison event, inserted as the table owner (the runtime role could not write a bad one
    # through the code, which is the point of the test: it is the pipeline that must cope).
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO graph_outbox (workspace_id, event_type, aggregate_id, payload) "
                "VALUES (CAST(:ws AS uuid), 'memory.state', gen_random_uuid(), "
                "CAST('{\"memory\": {\"id\": \"not-a-uuid\"}}' AS jsonb))"
            ),
            {"ws": ws},
        )
    # ...followed by a GOOD event for the same workspace, which lifts the watermark past it.
    session = await _as_owner(supabase_engine, data)
    try:
        with _flag(True):
            await emit_memory_states(session, [data.target_memory_id], reason="edit")
        await session.commit()
    finally:
        await session.close()

    state_cypher = "MATCH (w:Workspace {key: $workspace_id}) RETURN w.applied_seq AS applied, "
    state_cypher += "w.gap_seq AS gap"

    async def fresh() -> bool:
        async with supabase_engine.connect() as conn:
            return await reads._graph_fresh(conn, data.target_workspace_id)  # type: ignore[arg-type]

    async def gapped_and_caught_up() -> bool:
        try:  # the projector initialises the shared driver a moment after it starts
            rows = await run_read(state_cypher, ws)
        except Exception:  # noqa: BLE001
            return False
        if not rows or rows[0]["gap"] is None:
            return False
        async with supabase_engine.connect() as conn:
            published = (
                await conn.execute(
                    text(
                        "SELECT published_seq FROM graph_publication_cursor "
                        "WHERE workspace_id = CAST(:ws AS uuid)"
                    ),
                    {"ws": ws},
                )
            ).scalar()
        return published is not None and rows[0]["applied"] >= published

    async def repaired() -> bool:
        rows = await run_read(state_cypher, ws)
        return bool(rows) and rows[0]["gap"] is None and await fresh()

    stop = asyncio.Event()
    await graph.close_graph()
    relay_task = asyncio.create_task(relay.run(stop))
    projector_task = asyncio.create_task(projector.run(stop))
    try:
        await _wait_for(
            gapped_and_caught_up, timeout=90, what="the poison event to gap the workspace"
        )
        # Caught up, yet not trusted: this is exactly the case the watermark used to get wrong.
        assert await fresh() is False
        # ...and the rollout status tool says so in words an operator can act on.
        async with relay_engine.connect() as conn:
            (report,) = (
                await status.collect(conn, workspace_id=data.target_workspace_id)
            ).workspaces
        assert report.state == status.GAP and report.gap_seq is not None

        # The backfill CLI builds its own pool from settings; point it at the disposable database.
        monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
        get_settings.cache_clear()
        assert await backfill.run(data.target_workspace_id, data.owner_id) == 0
        await _wait_for(repaired, timeout=90, what="the completed backfill to clear the gap")
        async with relay_engine.connect() as conn:
            (report,) = (
                await status.collect(conn, workspace_id=data.target_workspace_id)
            ).workspaces
        assert report.state == status.TRUSTED and report.gap_seq is None
    finally:
        stop.set()
        assert await asyncio.wait_for(relay_task, timeout=30) == 0
        assert await asyncio.wait_for(projector_task, timeout=30) == 0
        await graph.init_graph()  # the projector closed the shared driver on the way out

    assert await fresh() is True
