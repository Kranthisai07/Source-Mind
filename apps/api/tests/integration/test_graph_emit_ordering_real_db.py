# ruff: noqa: F811  (pytest fixtures are used by parameter name)
"""A backfill must never overwrite a newer write (Codex review of #44, D-062).

An outbox event's ``seq`` is assigned at INSERT and the projector keeps the higher one. Without
the workspace lock in ``emit_memory_states``, a backfill could snapshot a memory, a concurrent
writer could commit a newer state, and the older snapshot would then be inserted with the higher
``seq``. These tests run the two sides on separate real connections and assert both orderings.

Needs the same disposable database and ``MIGRATION_DATABASE_URL`` as test_graph_outbox_real_db.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from sourcemind.events.backfill import backfill_workspace
from sourcemind.models.attribution import Attribution
from tests.integration.test_graph_outbox_real_db import (
    _as_owner,
    _flag,
    _rows,
    owner_engine,  # noqa: F401  (pytest fixture, used by name)
)
from tests.integration.test_security_foundation_real_db import _seed_security_tenants

pytestmark = pytest.mark.asyncio

HANDOFF_WEIGHT = 0.9137  # a value no seed data uses, so the assertion cannot match by accident


async def _handoff_only_change(session, data) -> None:
    """A contribution change that never touches the memories row (so a row lock would miss it)."""
    session.add(
        Attribution(
            memory_id=data.target_memory_id,
            user_id=data.owner_id,
            contribution_weight=HANDOFF_WEIGHT,
            trigger_action="handoff_assign",
        )
    )
    await session.flush()


def _weights(event: dict) -> list[float]:
    return [round(c["weight"], 4) for c in event["payload"]["contributors"]]


async def test_a_backfill_batch_waits_for_an_in_flight_writer_and_snapshots_its_state(  # noqa: F811
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    writer = await _as_owner(supabase_engine, data)
    backfiller = await _as_owner(supabase_engine, data)
    try:
        await _handoff_only_change(writer, data)
        with _flag(True):
            await emit_writer(writer, data)  # holds the shared lock until it commits

        batch = asyncio.create_task(backfill_workspace(backfiller, data.target_workspace_id))
        done, _ = await asyncio.wait({batch}, timeout=1.0)
        assert not done, "the backfill batch must wait while a writer's emit is uncommitted"

        await writer.commit()
        await asyncio.wait_for(batch, timeout=20)
    finally:
        await writer.close()
        await backfiller.close()

    events = await _rows(owner_engine, data.target_memory_id)
    by_reason = {e["payload"]["reason"]: e for e in events}
    assert by_reason["backfill"]["seq"] > by_reason["handoff_assign"]["seq"]
    # The backfill snapshot was read after the writer committed, so it already has the new weight:
    # the higher seq carries the newer state, not an older one.
    assert HANDOFF_WEIGHT in _weights(by_reason["backfill"])
    assert HANDOFF_WEIGHT in _weights(by_reason["handoff_assign"])


async def test_a_writer_arriving_mid_backfill_waits_and_gets_the_higher_seq(  # noqa: F811
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    backfiller = await _as_owner(supabase_engine, data)
    writer = await _as_owner(supabase_engine, data)
    try:
        # A backfill batch has emitted and not yet committed: it holds the lock exclusively.
        from sourcemind.services.graph.outbox import emit_memory_states

        await emit_memory_states(
            backfiller, [data.target_memory_id], reason="backfill", force=True
        )

        async def write() -> None:
            await _handoff_only_change(writer, data)
            with _flag(True):
                await emit_writer(writer, data)
            await writer.commit()

        task = asyncio.create_task(write())
        done, _ = await asyncio.wait({task}, timeout=1.0)
        assert not done, "a writer must wait for the backfill batch to commit"

        await backfiller.commit()
        await asyncio.wait_for(task, timeout=20)
    finally:
        await writer.close()
        await backfiller.close()

    events = await _rows(owner_engine, data.target_memory_id)
    by_reason = {e["payload"]["reason"]: e for e in events}
    assert by_reason["handoff_assign"]["seq"] > by_reason["backfill"]["seq"]
    assert HANDOFF_WEIGHT in _weights(by_reason["handoff_assign"])
    # The newest event for the memory is the writer's, so the projection ends on the new state.
    assert max(events, key=lambda e: e["seq"])["payload"]["reason"] == "handoff_assign"


async def test_writers_do_not_block_each_other(supabase_engine: AsyncEngine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    first = await _as_owner(supabase_engine, data)
    second = await _as_owner(supabase_engine, data)
    try:
        with _flag(True):
            await emit_writer(first, data)
            # A second writer's emit must complete while the first is still uncommitted.
            await asyncio.wait_for(emit_writer(second, data), timeout=5)
        await first.commit()
        await second.commit()
    finally:
        await first.close()
        await second.close()


async def emit_writer(session, data) -> None:
    from sourcemind.services.graph.outbox import emit_memory_states

    assert (
        await emit_memory_states(session, [data.target_memory_id], reason="handoff_assign") == 1
    )
