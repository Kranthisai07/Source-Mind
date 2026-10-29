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
from sourcemind.events import projector, relay
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
