"""The PostgreSQL side of the graph reads, and the freshness cursor, on a real database.

These are the answers the endpoints give whenever the graph is off, behind or disagrees, so they
have to be right on their own. They also pin the cursor the relay maintains and the runtime role's
read-only access to it. Needs the disposable PostgreSQL at migration 20261029_0016 (see conftest).
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.events import relay
from sourcemind.models.attribution import Attribution
from sourcemind.models.memory import Memory
from sourcemind.models.memory_relation import MemoryRelation
from sourcemind.services.graph import reads
from sourcemind.services.graph.outbox import emit_memory_states
from tests.integration.test_graph_outbox_real_db import (  # noqa: F401  (fixtures)
    _as_owner,
    _flag,
    _new_version,
    _rows,
    owner_engine,
    relay_engine,
)
from tests.integration.test_security_foundation_real_db import _seed_security_tenants

pytestmark = pytest.mark.asyncio


def _no_graph():
    return patch.object(reads, "graph_usable", return_value=False)


async def _scenario(session: AsyncSession, data) -> tuple[Memory, Memory]:
    """v2 (owner 0.75, member 0.5) extends the seeded v1; C (member 0.9) updates v2."""
    v2 = await _new_version(session, data, label="reads")
    session.add(
        Attribution(
            memory_id=v2.id,
            user_id=data.member_id,
            contribution_weight=0.5,
            trigger_action="edit",
        )
    )
    c = Memory(
        workspace_id=data.target_workspace_id,
        content="a third memory",
        content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
    )
    session.add(c)
    await session.flush()
    session.add(
        MemoryRelation(
            source_memory_id=c.id,
            target_memory_id=v2.id,
            relation_type="updates",
            similarity_score=0.8,
            confidence=0.8,
            detected_by="pipeline",
        )
    )
    session.add(
        Attribution(
            memory_id=c.id,
            user_id=data.member_id,
            contribution_weight=0.9,
            trigger_action="create",
        )
    )
    await session.flush()
    return v2, c


async def test_lineage_lists_the_chain_with_each_versions_contributors(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        v2, _ = await _scenario(session, data)
        with _no_graph():
            out = await reads.lineage(session, v2.id, data.target_workspace_id)
    finally:
        await session.rollback()
        await session.close()

    assert out.source == "postgres"
    assert [(v.version, v.is_current) for v in out.versions] == [(2, True), (1, True)]
    head, root = out.versions
    assert {str(c.user_id): c.weight for c in head.contributors} == {
        str(data.owner_id): 0.75,
        str(data.member_id): 0.5,
    }
    assert [c.weight for c in head.contributors] == [0.75, 0.5]  # heaviest first
    assert root.contributors == []


async def test_a_deleted_parent_ends_the_chain_like_the_existing_versions_route(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        v2, _ = await _scenario(session, data)
        await session.execute(
            text("UPDATE memories SET deleted_at = now() WHERE id = CAST(:id AS uuid)"),
            {"id": str(data.target_memory_id)},
        )
        with _no_graph():
            out = await reads.lineage(session, v2.id, data.target_workspace_id)
    finally:
        await session.rollback()
        await session.close()
    assert [v.version for v in out.versions] == [2]


async def test_related_falls_back_to_one_hop_with_the_induced_edges_and_type_filter(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        v2, c = await _scenario(session, data)
        with _no_graph():
            everything = await reads.related(session, v2.id, data.target_workspace_id, depth=3)
            updates_only = await reads.related(
                session, v2.id, data.target_workspace_id, depth=3, types=["updates"]
            )
            await session.execute(
                text("UPDATE memories SET deleted_at = now() WHERE id = CAST(:id AS uuid)"),
                {"id": str(c.id)},
            )
            without_deleted = await reads.related(session, v2.id, data.target_workspace_id)
    finally:
        await session.rollback()
        await session.close()

    assert everything.source == "postgres"
    assert (everything.depth_requested, everything.depth_served) == (3, 1)
    assert {str(n.id): n.hops for n in everything.nodes} == {
        str(v2.id): 0,
        str(data.target_memory_id): 1,
        str(c.id): 1,
    }
    assert {e.type for e in everything.edges} == {"extends", "updates"}
    assert {str(n.id) for n in updates_only.nodes} == {str(v2.id), str(c.id)}
    assert {e.type for e in updates_only.edges} == {"updates"}
    assert str(c.id) not in {str(n.id) for n in without_deleted.nodes}  # deleted memories vanish
    assert all(len(n.preview) <= 120 for n in everything.nodes)


async def test_owners_fall_back_to_the_direct_contributors_heaviest_first(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        v2, _ = await _scenario(session, data)
        with _no_graph():
            out = await reads.owners(session, v2.id, data.target_workspace_id)
    finally:
        await session.rollback()
        await session.close()
    assert out.source == "postgres" and out.depth_served == 0
    assert [(str(o.user_id), o.direct_weight, o.hops) for o in out.owners] == [
        (str(data.owner_id), 0.75, 0),
        (str(data.member_id), 0.5, 0),
    ]


async def test_collaboration_counts_current_memories_and_shared_ones(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        await _scenario(session, data)
        with _no_graph():
            out = await reads.collaboration(session, data.target_workspace_id)
    finally:
        await session.rollback()
        await session.close()
    counts = {str(n.user_id): n.memory_count for n in out.nodes}
    assert counts == {str(data.owner_id): 1, str(data.member_id): 2}
    (edge,) = out.edges
    assert {str(edge.source), str(edge.target)} == {str(data.owner_id), str(data.member_id)}
    assert str(edge.source) < str(edge.target) and edge.shared_memories == 1


async def test_another_workspaces_memory_is_invisible_through_every_read(
    supabase_engine: AsyncEngine,
) -> None:
    """The owner is in BOTH workspaces, so only the explicit workspace filter separates them."""
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        ws = data.target_workspace_id
        with _no_graph():
            assert (await reads.lineage(session, data.other_memory_id, ws)).versions == []
            assert (await reads.related(session, data.other_memory_id, ws)).nodes == []
            assert (await reads.owners(session, data.other_memory_id, ws)).owners == []
        assert await reads._memory_rows(session, ws, [str(data.other_memory_id)]) == {}  # noqa: SLF001
    finally:
        await session.rollback()
        await session.close()


async def test_a_workspace_with_no_cursor_is_never_fresh(supabase_engine: AsyncEngine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        assert await reads._graph_fresh(session, data.target_workspace_id) is False  # noqa: SLF001
    finally:
        await session.rollback()
        await session.close()


async def test_the_runtime_role_can_read_but_not_write_the_cursor(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        await session.execute(text("SELECT count(*) FROM graph_publication_cursor"))
        for statement in (
            "INSERT INTO graph_publication_cursor (workspace_id, published_seq) "
            "VALUES (gen_random_uuid(), 1)",
            "UPDATE graph_publication_cursor SET published_seq = 0",
            "DELETE FROM graph_publication_cursor",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin_nested():
                    await session.execute(text(statement))
    finally:
        await session.rollback()
        await session.close()


class _Publisher:
    async def publish(self, envelope) -> None:
        return None


async def test_the_relay_advances_each_workspaces_cursor_and_never_moves_it_back(
    supabase_engine: AsyncEngine,
    owner_engine: AsyncEngine,  # noqa: F811
    relay_engine: AsyncEngine,  # noqa: F811
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        v2, _ = await _scenario(session, data)
        with _flag(True):
            await emit_memory_states(session, [v2.id, data.target_memory_id], reason="edit")
        await session.commit()
    finally:
        await session.close()

    async with relay_engine.connect() as conn:
        await relay.publish_pending(conn, _Publisher(), batch_size=10_000, settle_seconds=0)

    mine = [
        r["seq"]
        for aggregate in (v2.id, data.target_memory_id)
        for r in await _rows(owner_engine, aggregate)
    ]

    async def cursor() -> int | None:
        async with relay_engine.connect() as conn:
            return (
                await conn.execute(
                    text(
                        "SELECT published_seq FROM graph_publication_cursor "
                        "WHERE workspace_id = CAST(:ws AS uuid)"
                    ),
                    {"ws": str(data.target_workspace_id)},
                )
            ).scalar()

    assert await cursor() == max(mine)

    # Marking an older seq again (a replay, or a late batch) must not pull the cursor back.
    async with relay_engine.connect() as conn:
        await relay.mark_published(conn, [min(mine)])
        await conn.commit()
    assert await cursor() == max(mine)
