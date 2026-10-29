"""The graph outbox against a real PostgreSQL: atomicity, tenancy, grants, relay and backfill.

Needs the disposable test database (``SECURITY_TEST_ALLOW_DISPOSABLE=1``, see conftest) with
migration 20261029_0015 applied. Reading ``graph_outbox`` needs a role that is allowed to, which
the runtime role deliberately is not, so these tests read it through two extra connections that
are skipped when their URLs are not configured:

* ``MIGRATION_DATABASE_URL``: the table owner (verification of what was written);
* ``TEST_GRAPH_RELAY_DATABASE_URL``: the relay role (SELECT and UPDATE of ``published_at`` only).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.events import relay
from sourcemind.models.attribution import Attribution
from sourcemind.models.memory import Memory
from sourcemind.models.memory_relation import MemoryRelation
from sourcemind.services.graph.outbox import emit_memory_states
from sourcemind.events.backfill import backfill_workspace
from tests.integration.test_security_foundation_real_db import _seed_security_tenants

pytestmark = pytest.mark.asyncio


def _loopback_engine(env_name: str) -> AsyncEngine:
    url = os.environ.get(env_name)
    if not url:
        pytest.skip(f"{env_name} is not configured")
    parsed = urlparse(url.replace("+asyncpg", ""))
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(f"refusing non-loopback {env_name}")
    return create_async_engine(url, pool_size=2, max_overflow=0)


@pytest_asyncio.fixture
async def owner_engine() -> AsyncIterator[AsyncEngine]:
    engine = _loopback_engine("MIGRATION_DATABASE_URL")
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def relay_engine() -> AsyncIterator[AsyncEngine]:
    engine = _loopback_engine("TEST_GRAPH_RELAY_DATABASE_URL")
    try:
        yield engine
    finally:
        await engine.dispose()


def _flag(on: bool):
    return patch(
        "sourcemind.services.graph.outbox.get_settings",
        return_value=SimpleNamespace(ff_kafka_events=on),
    )


async def _rows(engine: AsyncEngine, aggregate_id: uuid.UUID) -> list[dict]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT seq, workspace_id::text AS workspace_id, event_type, payload, "
                "published_at FROM graph_outbox WHERE aggregate_id = CAST(:a AS uuid) ORDER BY seq"
            ),
            {"a": str(aggregate_id)},
        )
        return [dict(row) for row in result.mappings().all()]


async def _new_version(session: AsyncSession, data, *, label: str) -> Memory:
    """A version 2 of the seeded target memory, with a contribution and a relation."""
    memory = Memory(
        workspace_id=data.target_workspace_id,
        content=f"SECRET CONTENT {label}",
        content_hash=hashlib.sha256(label.encode() + uuid.uuid4().bytes).hexdigest(),
        parent_memory_id=data.target_memory_id,
        version=2,
    )
    session.add(memory)
    await session.flush()
    session.add(
        Attribution(
            memory_id=memory.id,
            user_id=data.owner_id,
            contribution_weight=0.75,
            trigger_action="edit",
        )
    )
    session.add(
        MemoryRelation(
            source_memory_id=memory.id,
            target_memory_id=data.target_memory_id,
            relation_type="extends",
            similarity_score=0.9,
            confidence=0.9,
            detected_by="pipeline",
        )
    )
    await session.flush()
    return memory


async def _as_owner(engine: AsyncEngine, data) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(session, data.owner_id)
    await set_rls_workspace_context(session, data.target_workspace_id)
    return session


async def test_events_commit_atomically_with_the_data_and_carry_no_content(
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        memory = await _new_version(session, data, label="atomic")
        with _flag(True):
            written = await emit_memory_states(
                session, [memory.id, data.target_memory_id], reason="edit"
            )
        await session.commit()
    finally:
        await session.close()

    assert written == 2
    (event,) = await _rows(owner_engine, memory.id)
    assert event["workspace_id"] == str(data.target_workspace_id)
    assert event["event_type"] == "memory.state" and event["published_at"] is None
    payload = event["payload"]
    assert payload["memory"]["version"] == 2
    assert payload["memory"]["parent_id"] == str(data.target_memory_id)
    assert payload["contributors"] == [
        {"user_id": str(data.owner_id), "weight": 0.75, "action": "edit", "edit_id": None}
    ]
    assert [r["target"] for r in payload["relations_out"]] == [str(data.target_memory_id)]
    assert "SECRET CONTENT" not in json.dumps(payload)
    # The superseded head is emitted too. The edge new -> old is already in the new memory's
    # outgoing set (authoritative), so it is not repeated to the old one as an incoming hint.
    (old,) = await _rows(owner_engine, data.target_memory_id)
    assert old["payload"]["memory"]["id"] == str(data.target_memory_id)
    assert old["payload"]["relations_in"] == []


async def test_a_rolled_back_change_leaves_no_event(
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        memory = await _new_version(session, data, label="rollback")
        with _flag(True):
            await emit_memory_states(session, [memory.id], reason="edit")
        await session.rollback()
    finally:
        await session.close()
    assert await _rows(owner_engine, memory.id) == []


async def test_a_memory_the_caller_cannot_see_is_not_projected(
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    async with AsyncSession(supabase_engine, expire_on_commit=False) as outsider:
        await set_rls_user_context(outsider, data.other_workspace_member_id)
        with _flag(True):
            written = await emit_memory_states(outsider, [data.target_memory_id], reason="edit")
        await outsider.commit()
    assert written == 0
    assert await _rows(owner_engine, data.target_memory_id) == []


async def test_the_runtime_role_can_insert_but_not_read_the_outbox(
    supabase_engine: AsyncEngine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        with _flag(True):
            assert await emit_memory_states(session, [data.target_memory_id], reason="edit") == 1
        with pytest.raises(DBAPIError, match="permission denied"):
            await session.execute(text("SELECT count(*) FROM graph_outbox"))
    finally:
        await session.rollback()
        await session.close()


async def test_the_flag_off_writes_nothing_even_for_a_real_change(
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        memory = await _new_version(session, data, label="flag-off")
        with _flag(False):
            assert await emit_memory_states(session, [memory.id], reason="edit") == 0
        await session.commit()
    finally:
        await session.close()
    assert await _rows(owner_engine, memory.id) == []


class _Publisher:
    def __init__(self, fail_on_workspace: str | None = None) -> None:
        self.seqs: list[int] = []
        self._fail = fail_on_workspace

    async def publish(self, envelope) -> None:
        self.seqs.append(envelope["seq"])


async def test_relay_publishes_in_seq_order_once_rows_settle(
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine, relay_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        memory = await _new_version(session, data, label="relay")
        with _flag(True):
            await emit_memory_states(
                session, [memory.id, data.target_memory_id], reason="edit"
            )
        await session.commit()
    finally:
        await session.close()

    async with relay_engine.connect() as conn:
        # A long settle window: nothing is old enough, so nothing is published.
        held = _Publisher()
        assert await relay.publish_pending(conn, held, batch_size=500, settle_seconds=3600) == 0
        assert held.seqs == []

    async with relay_engine.connect() as conn:
        live = _Publisher()
        sent = await relay.publish_pending(conn, live, batch_size=10_000, settle_seconds=0)
    assert sent >= 2 and live.seqs == sorted(live.seqs)

    for aggregate in (memory.id, data.target_memory_id):
        (row,) = await _rows(owner_engine, aggregate)
        assert row["published_at"] is not None and row["published_at"] <= datetime.now(UTC)

    async with relay_engine.connect() as conn:
        assert await relay.publish_pending(
            conn, _Publisher(), batch_size=10_000, settle_seconds=0
        ) == 0  # already published rows are not sent again


async def test_a_second_relay_cannot_take_the_advisory_lock(relay_engine: AsyncEngine) -> None:
    async with relay_engine.connect() as first, relay_engine.connect() as second:
        assert await relay.acquire_singleton(first) is True
        assert await relay.acquire_singleton(second) is False


async def test_backfill_reemits_every_memory_including_deleted_ones(
    supabase_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    session = await _as_owner(supabase_engine, data)
    try:
        gone = await _new_version(session, data, label="deleted")
        await session.execute(
            text(
                "UPDATE memories SET deleted_at = now(), deleted_reason = 'user_deleted' "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"id": str(gone.id)},
        )
        await session.commit()
        with _flag(False):  # backfill is run on purpose, so it must not depend on the flag
            total = await backfill_workspace(session, data.target_workspace_id, batch_size=1)
    finally:
        await session.close()

    assert total >= 2
    (event,) = await _rows(owner_engine, gone.id)
    assert event["payload"]["memory"]["deleted"] is True
    assert event["payload"]["memory"]["deleted_reason"] == "user_deleted"
    assert event["payload"]["reason"] == "backfill"
    # Nothing from the other workspace leaks into this backfill.
    assert await _rows(owner_engine, data.other_memory_id) == []
