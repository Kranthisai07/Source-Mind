"""Revision handoff, fenced publication, disconnect checks, caps and budget (real DB).

Runs as the restricted runtime role against the disposable PostgreSQL and Redis,
through the real ``receive`` (handoff) and the real ``_run_pipeline`` (publication).
Only the model providers (extraction, embedding, relation planning) are stubbed by
the worker-contract harness; nothing here is a real GitHub or provider call.

Contract: docs/architecture/AUTOMATIC_INGESTION_CONTRACT.md (C6, C8, C10, Bounds).
"""
# The worker-contract fixtures are imported (not copied) so both suites share
# one harness; pytest resolves them by parameter name, which ruff reads as F811.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.core.config import get_settings
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.redis_client import init_redis
from sourcemind.services.ingestion.fact_extractor import ExtractionResult as FactExtractionResult
from tests.integration.test_ingestion_worker_contract import (  # noqa: F401 - fixtures
    FakeTask,
    WorkerHarness,
    _submit,
    ingestion_worker_database_url,
    ingestion_worker_engine,
    ingestion_worker_environment,
    ingestion_worker_redis_url,
    worker_harness,
)
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    from sourcemind.workers import ingestion as worker

    monkeypatch.setattr(
        worker.process_document,
        "apply_async",
        lambda **kwargs: SimpleNamespace(id=kwargs["task_id"]),
    )


class Retire:
    """Recording fake for retire_document_memories."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, session: AsyncSession, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(retired_ids=[], flagged_ids=[])


def _install_retire(monkeypatch: pytest.MonkeyPatch, impl: Any) -> None:
    from sourcemind.services.memory import retire

    monkeypatch.setattr(retire, "retire_document_memories", impl)


async def _local_retire(session: AsyncSession, *, workspace_id, document_id, reason, now=None):
    """Minimal stand-in for the real retirement: soft-deletes the document's memories."""
    await session.execute(
        text(
            "UPDATE memories SET deleted_at = now(), deleted_reason = :r "
            "WHERE document_id = CAST(:d AS uuid) AND deleted_at IS NULL"
        ),
        {"r": reason, "d": str(document_id)},
    )
    return SimpleNamespace(retired_ids=[], flagged_ids=[])


async def _session(
    engine: AsyncEngine, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(session, user_id)
    await set_rls_workspace_context(session, workspace_id)
    return session


async def _connector(engine: AsyncEngine, data: SecurityTenantData, status: str = "active"):
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        cid = await session.scalar(
            text(
                "INSERT INTO connector_configs (workspace_id, connector_type, display_name, "
                "status) VALUES (CAST(:w AS uuid), 'github', :n, :s) RETURNING id"
            ),
            {"w": str(data.target_workspace_id), "n": f"c-{uuid.uuid4()}", "s": status},
        )
        await session.commit()
    return uuid.UUID(str(cid))


async def _disconnect(engine: AsyncEngine, data: SecurityTenantData, connector_id: uuid.UUID):
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        await session.execute(
            text(
                "UPDATE connector_configs SET generation = generation + 1, "
                "fence = fence + 1, status = 'disconnected' WHERE id = CAST(:c AS uuid)"
            ),
            {"c": str(connector_id)},
        )
        await session.commit()


def _sid() -> str:
    return f"acme/repo/issues/{uuid.uuid4().hex[:12]}"


def _text(label: str = "body") -> str:
    # Longer than the verbatim threshold so extraction mode is natural.
    return f"{label} {uuid.uuid4().hex} " + "x" * 50


async def _handoff(
    engine: AsyncEngine,
    data: SecurityTenantData,
    connector_id: uuid.UUID,
    source_id: str,
    content: str,
    *,
    mode: str = "extract",
    generation: int = 0,
    pre_commit: Any = None,
    source_type: str = "issue",
) -> dict[str, Any]:
    from sourcemind.services.ingestion.receiver import receive

    await init_redis()
    session = await _session(engine, data.member_id, data.target_workspace_id)
    async with session:
        return await receive(
            session=session,
            workspace_id=data.target_workspace_id,
            user_id=data.member_id,
            content=content,
            title="t",
            ingestion_mode=mode,
            idempotency_key=uuid.uuid4().hex,
            artifact_link={
                "source_tool": "github",
                "source_type": source_type,
                "source_id": source_id,
                "source_url": None,
                "source_author": "alice",
                "source_timestamp": None,
                "artifact_metadata": {},
                "source_revision": hashlib.sha256(content.encode()).hexdigest(),
                "connector_id": connector_id,
                "connector_generation": generation,
            },
            context={"repo": "acme/repo", "number": 7},
            pre_commit=pre_commit,
        )


async def _run(data: SecurityTenantData, document_id: Any) -> dict[str, Any]:
    from sourcemind.workers.ingestion import _run_pipeline

    return await _run_pipeline(
        FakeTask(retries=0),
        str(document_id),
        str(data.target_workspace_id),
        str(data.member_id),
    )


def _facts(harness: WorkerHarness, facts: list[str], *, failed: int = 0, usage=None) -> list:
    """Stub fact extraction; returns the list that records each call's kwargs."""
    calls: list[dict[str, Any]] = []

    async def extract_facts(chunks: list, *_a: object, **kwargs: object) -> FactExtractionResult:
        harness.fact_extraction_calls += 1
        calls.append({"chunks": chunks, **kwargs})
        return FactExtractionResult(
            facts=facts,
            total_chunks=max(1, len(chunks)),
            failed_chunks=failed,
            failure_reasons=["synthetic chunk failure"] * failed,
            usage=usage,
        )

    harness.extract_facts = extract_facts  # type: ignore[method-assign]
    return calls


def _fact(label: str = "f") -> str:
    return f"Fact {label} {uuid.uuid4().hex}."


async def _rows(engine: AsyncEngine, data: SecurityTenantData, sql: str, **params: Any):
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        return (await session.execute(text(sql), params)).fetchall()


async def _doc(engine, data, document_id) -> dict[str, Any]:
    (row,) = await _rows(
        engine,
        data,
        "SELECT ingestion_status, memory_count, supersedes_document_id, connector_id, "
        "connector_generation, sha256_hash, metadata FROM documents WHERE id = CAST(:d AS uuid)",
        d=str(document_id),
    )
    meta = row.metadata
    return {
        "status": row.ingestion_status,
        "memory_count": row.memory_count,
        "supersedes": row.supersedes_document_id,
        "connector_id": row.connector_id,
        "generation": row.connector_generation,
        "hash": row.sha256_hash,
        "meta": json.loads(meta) if isinstance(meta, str) else meta,
    }


async def _head(engine, data, source_id) -> Any:
    rows = await _rows(
        engine,
        data,
        "SELECT latest_seq, published_seq, published_document_id FROM artifact_heads "
        "WHERE source_id = :s",
        s=source_id,
    )
    return rows[0] if rows else None


async def _live(engine, data, document_id) -> int:
    (row,) = await _rows(
        engine,
        data,
        "SELECT count(*) AS n FROM memories WHERE document_id = CAST(:d AS uuid) "
        "AND deleted_at IS NULL",
        d=str(document_id),
    )
    return int(row.n)


async def _total_memories(engine, data, document_id) -> int:
    (row,) = await _rows(
        engine,
        data,
        "SELECT count(*) AS n FROM memories WHERE document_id = CAST(:d AS uuid)",
        d=str(document_id),
    )
    return int(row.n)


@pytest_asyncio.fixture
async def world(ingestion_worker_engine, worker_harness, monkeypatch):
    data = await _seed_security_tenants(ingestion_worker_engine)
    connector_id = await _connector(ingestion_worker_engine, data)
    return SimpleNamespace(
        engine=ingestion_worker_engine,
        data=data,
        harness=worker_harness,
        connector=connector_id,
        mp=monkeypatch,
    )


async def _two_revisions(w, source_id=None):
    sid = source_id or _sid()
    r1 = await _handoff(w.engine, w.data, w.connector, sid, _text("rev1"))
    r2 = await _handoff(w.engine, w.data, w.connector, sid, _text("rev2"))
    return sid, uuid.UUID(r1["document_id"]), uuid.UUID(r2["document_id"])


# ── handoff ──────────────────────────────────────────────────────────────────


async def test_handoff_same_revision_is_skipped(world) -> None:
    w = world
    sid = _sid()
    content = _text()
    first = await _handoff(w.engine, w.data, w.connector, sid, content)
    again = await _handoff(w.engine, w.data, w.connector, sid, content)

    assert first["revision_seq"] == 1 and first["already_exists"] is False
    assert again["already_exists"] is True and again["reason"] == "same_revision"
    assert again["document_id"] == first["document_id"]
    assert again["revision_seq"] == 1
    docs = await _rows(
        w.engine, w.data, "SELECT id FROM artifact_links WHERE source_id = :s", s=sid
    )
    assert len(docs) == 1
    head = await _head(w.engine, w.data, sid)
    assert head.latest_seq == 1


async def test_handoff_new_revision_allocates_seq_and_supersedes(world) -> None:
    w = world
    sid = _sid()
    r1 = await _handoff(w.engine, w.data, w.connector, sid, _text("a"))
    r2 = await _handoff(w.engine, w.data, w.connector, sid, _text("b"))
    d2 = await _doc(w.engine, w.data, r2["document_id"])

    assert (r1["revision_seq"], r2["revision_seq"]) == (1, 2)
    assert str(d2["supersedes"]) == r1["document_id"]
    assert d2["connector_id"] == w.connector and d2["generation"] == 0
    assert d2["meta"]["context"] == {"repo": "acme/repo", "number": 7}
    assert "repo" not in d2["meta"]["raw_content"]  # labels never join the stored text
    assert (await _head(w.engine, w.data, sid)).latest_seq == 2
    seqs = await _rows(
        w.engine, w.data, "SELECT revision_seq FROM artifact_links WHERE source_id = :s", s=sid
    )
    assert sorted(r.revision_seq for r in seqs) == [1, 2]


async def test_handoff_returning_to_earlier_text_is_a_new_revision(world) -> None:
    w = world
    sid = _sid()
    a, b = _text("a"), _text("b")
    await _handoff(w.engine, w.data, w.connector, sid, a)
    await _handoff(w.engine, w.data, w.connector, sid, b)
    back = await _handoff(w.engine, w.data, w.connector, sid, a)
    assert back["already_exists"] is False and back["revision_seq"] == 3


async def test_handoff_pre_commit_runs_and_raising_rolls_everything_back(world) -> None:
    w = world
    ran: list[bool] = []

    async def ok(_session: AsyncSession) -> None:
        ran.append(True)

    await _handoff(w.engine, w.data, w.connector, _sid(), _text(), pre_commit=ok)
    assert ran == [True]

    sid = _sid()
    rolled_back_content = _text("rolledback")

    async def boom(session: AsyncSession) -> None:
        # Everything is already written in this transaction when pre_commit runs.
        n = await session.scalar(
            text("SELECT count(*) FROM artifact_links WHERE source_id = :s"), {"s": sid}
        )
        assert n == 1
        raise RuntimeError("lease lost")

    with pytest.raises(RuntimeError, match="lease lost"):
        await _handoff(
            w.engine, w.data, w.connector, sid, rolled_back_content, pre_commit=boom
        )

    assert await _head(w.engine, w.data, sid) is None
    assert not await _rows(
        w.engine, w.data, "SELECT 1 FROM artifact_links WHERE source_id = :s", s=sid
    )
    assert not await _rows(
        w.engine,
        w.data,
        "SELECT 1 FROM documents WHERE metadata->>'raw_content' = :c",
        c=rolled_back_content,
    )


async def test_salted_hash_keeps_both_provenance_rows_for_identical_text(world) -> None:
    w = world
    content = _text("shared")
    s1, s2 = _sid(), _sid()
    r1 = await _handoff(w.engine, w.data, w.connector, s1, content)
    r2 = await _handoff(w.engine, w.data, w.connector, s2, content)

    assert r1["document_id"] != r2["document_id"]
    d1 = await _doc(w.engine, w.data, r1["document_id"])
    d2 = await _doc(w.engine, w.data, r2["document_id"])
    assert d1["hash"] != d2["hash"]
    links = await _rows(
        w.engine,
        w.data,
        "SELECT source_id FROM artifact_links WHERE source_id = ANY(CAST(:ids AS text[]))",
        ids=[s1, s2],
    )
    assert sorted(r.source_id for r in links) == sorted([s1, s2])


# ── publication ──────────────────────────────────────────────────────────────


async def test_p1_newer_revision_publishes_first_then_older_publishes_nothing(world) -> None:
    w = world
    retire = Retire()
    _install_retire(w.mp, retire)
    _facts(w.harness, [_fact("rev")])
    sid, d1, d2 = await _two_revisions(w)

    out2 = await _run(w.data, d2)
    out1 = await _run(w.data, d1)

    assert out2["status"] == "completed" and out2["memories_created"] == 1
    assert out1["status"] == "completed" and out1["memories_created"] == 0
    doc1 = await _doc(w.engine, w.data, d1)
    assert doc1["status"] == "completed"
    assert doc1["meta"]["publication"] == "superseded_before_publish"
    assert (await _doc(w.engine, w.data, d2))["meta"]["publication"] == "published"
    assert await _live(w.engine, w.data, d1) == 0 and await _live(w.engine, w.data, d2) == 1
    head = await _head(w.engine, w.data, sid)
    assert (head.latest_seq, head.published_seq, head.published_document_id) == (2, 2, d2)
    assert retire.calls == []  # nothing had been published before rev2


async def test_p2_previous_revision_retired_only_at_new_publication_recording(world) -> None:
    w = world
    retire = Retire()
    _install_retire(w.mp, retire)
    _facts(w.harness, [_fact("rev")])
    sid = _sid()
    r1 = await _handoff(w.engine, w.data, w.connector, sid, _text("a"))
    d1 = uuid.UUID(r1["document_id"])
    await _run(w.data, d1)
    r2 = await _handoff(w.engine, w.data, w.connector, sid, _text("b"))
    d2 = uuid.UUID(r2["document_id"])

    # Handing rev2 off retires nothing; rev1 stays live until rev2 publishes.
    assert retire.calls == [] and await _live(w.engine, w.data, d1) == 1
    out = await _run(w.data, d2)

    assert out["memories_created"] == 1
    assert len(retire.calls) == 1
    call = retire.calls[0]
    assert call["document_id"] == d1 and call["reason"] == "source_superseded"
    assert call["workspace_id"] == w.data.target_workspace_id
    head = await _head(w.engine, w.data, sid)
    assert (head.published_seq, head.published_document_id) == (2, d2)


async def test_p2_previous_revision_retired_with_local_implementation(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact("rev")])
    sid = _sid()
    d1 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("a")))["document_id"])
    await _run(w.data, d1)
    d2 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("b")))["document_id"])
    assert await _live(w.engine, w.data, d1) == 1
    await _run(w.data, d2)

    assert await _live(w.engine, w.data, d1) == 0
    assert await _total_memories(w.engine, w.data, d1) == 1  # soft-deleted, not removed
    assert await _live(w.engine, w.data, d2) == 1


async def test_p3_partial_extraction_keeps_prior_live_and_retry_publishes(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact("rev1")])
    sid = _sid()
    d1 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("a")))["document_id"])
    await _run(w.data, d1)
    d2 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("b")))["document_id"])

    _facts(w.harness, [_fact("rev2")], failed=1)  # one chunk failed, one produced facts
    out = await _run(w.data, d2)

    assert out["status"] == "failed" and out["error_code"] == "incomplete_extraction"
    doc2 = await _doc(w.engine, w.data, d2)
    assert doc2["status"] == "failed"
    assert doc2["meta"]["error_code"] == "incomplete_extraction"
    assert doc2["meta"]["attempts"] == 0 and doc2["meta"]["next_retry_at"]
    assert await _live(w.engine, w.data, d1) == 1
    assert await _total_memories(w.engine, w.data, d2) == 0
    assert (await _head(w.engine, w.data, sid)).published_seq == 1

    from sourcemind.services.ingestion.receiver import requeue_transient_failure

    session = await _session(w.engine, w.data.member_id, w.data.target_workspace_id)
    async with session:
        assert await requeue_transient_failure(
            session, d2, w.data.target_workspace_id, now=datetime.now(UTC)
        )
    requeued = await _doc(w.engine, w.data, d2)
    assert requeued["status"] == "pending"
    assert requeued["meta"]["attempts"] == 1 and "error_code" not in requeued["meta"]

    _facts(w.harness, [_fact("rev2-ok")])
    retry = await _run(w.data, d2)
    assert retry["status"] == "completed" and retry["memories_created"] == 1
    assert await _live(w.engine, w.data, d1) == 0 and await _live(w.engine, w.data, d2) == 1
    assert (await _head(w.engine, w.data, sid)).published_seq == 2


async def test_p4_zero_fact_revision_keeps_prior_and_flags(world) -> None:
    w = world
    retire = Retire()
    _install_retire(w.mp, retire)
    _facts(w.harness, [_fact("rev1")])
    sid = _sid()
    d1 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("a")))["document_id"])
    await _run(w.data, d1)
    d2 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("b")))["document_id"])

    _facts(w.harness, [])
    out = await _run(w.data, d2)

    assert out["status"] == "completed" and out["memories_created"] == 0
    doc2 = await _doc(w.engine, w.data, d2)
    assert doc2["status"] == "completed" and doc2["meta"]["publication"] == "no_facts"
    assert await _live(w.engine, w.data, d1) == 1
    assert retire.calls == []
    assert (await _head(w.engine, w.data, sid)).published_seq == 1


async def test_p5_duplicate_finalize_is_idempotent(world) -> None:
    w = world
    retire = Retire()
    _install_retire(w.mp, retire)
    _facts(w.harness, [_fact("rev1")])
    sid = _sid()
    d1 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("a")))["document_id"])
    await _run(w.data, d1)
    d2 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("b")))["document_id"])
    first = await _run(w.data, d2)
    again = await _run(w.data, d2)

    assert first["memories_created"] == 1
    assert again["already_completed"] is True
    assert len(retire.calls) == 1
    assert await _live(w.engine, w.data, d2) == 1
    assert await _total_memories(w.engine, w.data, d2) == 1


async def test_p6_concurrent_finalize_is_serialized_by_the_head_lock(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact("rev")])
    sid, d1, d2 = await _two_revisions(w)

    # Hold the head lock so both finalizations queue on it, then release.
    holder = await _session(w.engine, w.data.owner_id, w.data.target_workspace_id)
    async with holder:
        await holder.execute(
            text("SELECT 1 FROM artifact_heads WHERE source_id = :s FOR UPDATE"), {"s": sid}
        )
        tasks = [asyncio.create_task(_run(w.data, d)) for d in (d1, d2)]
        await asyncio.sleep(3.0)
        assert not any(t.done() for t in tasks), "both must wait for the head lock"
        await holder.rollback()
    out1, out2 = await asyncio.wait_for(asyncio.gather(*tasks), timeout=60)

    assert out1["status"] == out2["status"] == "completed"
    assert await _live(w.engine, w.data, d1) == 0
    assert await _live(w.engine, w.data, d2) == 1
    head = await _head(w.engine, w.data, sid)
    assert (head.published_seq, head.published_document_id) == (2, d2)


async def test_p6_publication_waits_for_the_head_lock(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact("rev")])
    sid = _sid()
    d1 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text("a")))["document_id"])

    holder = await _session(w.engine, w.data.owner_id, w.data.target_workspace_id)
    async with holder:
        await holder.execute(
            text("SELECT 1 FROM artifact_heads WHERE source_id = :s FOR UPDATE"), {"s": sid}
        )
        task = asyncio.create_task(_run(w.data, d1))
        await asyncio.sleep(1.0)
        assert not task.done(), "publication must wait for the head lock"
        assert await _total_memories(w.engine, w.data, d1) == 0
        await holder.rollback()
    out = await asyncio.wait_for(task, timeout=30)
    assert out["status"] == "completed" and out["memories_created"] == 1


async def test_p7_first_revision_with_a_failed_chunk_is_not_half_published(world) -> None:
    w = world
    retire = Retire()
    _install_retire(w.mp, retire)
    _facts(w.harness, [_fact("partial")], failed=1)
    sid = _sid()
    d1 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text()))["document_id"])
    out = await _run(w.data, d1)

    assert out["status"] == "failed" and out["error_code"] == "incomplete_extraction"
    assert await _total_memories(w.engine, w.data, d1) == 0
    assert (await _head(w.engine, w.data, sid)).published_seq == 0
    links = await _rows(
        w.engine,
        w.data,
        "SELECT memory_id FROM artifact_links WHERE document_id = CAST(:d AS uuid)",
        d=str(d1),
    )
    assert [r.memory_id for r in links] == [None]
    assert retire.calls == []


async def test_provider_failure_is_transient_after_retries_exhausted(world) -> None:
    w = world
    _facts(w.harness, [_fact()])
    d1 = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"]
    )
    w.harness.transient_failures = 99
    from sourcemind.workers.ingestion import _run_pipeline

    with pytest.raises(RuntimeError):
        await _run_pipeline(
            FakeTask(retries=3),
            str(d1),
            str(w.data.target_workspace_id),
            str(w.data.member_id),
        )
    doc = await _doc(w.engine, w.data, d1)
    assert doc["status"] == "failed"
    assert doc["meta"]["error_code"] == "transient"
    assert doc["meta"]["attempts"] == 0 and doc["meta"]["next_retry_at"]


# ── mode crossing (T-S5 / T-S6, worker side) ─────────────────────────────────


async def test_s5_verbatim_then_extract_revision(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    short = f"Short note {uuid.uuid4().hex}"
    _facts(w.harness, [_fact("long")])
    sid = _sid()
    d1 = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, sid, short, mode="verbatim"))[
            "document_id"
        ]
    )
    r1 = await _run(w.data, d1)
    r = await _handoff(w.engine, w.data, w.connector, sid, _text("long"))
    d2 = uuid.UUID(r["document_id"])
    r2 = await _run(w.data, d2)

    assert r1["memories_created"] == 1 and r2["memories_created"] == 1
    content = await _rows(
        w.engine,
        w.data,
        "SELECT content FROM memories WHERE document_id = CAST(:d AS uuid)",
        d=str(d1),
    )
    assert content[0].content == short  # exact authored text
    assert await _live(w.engine, w.data, d1) == 0 and await _live(w.engine, w.data, d2) == 1


async def test_s6_extract_then_verbatim_revision_and_verbatim_never_no_facts(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact("long")])
    sid = _sid()
    r = await _handoff(w.engine, w.data, w.connector, sid, _text("long"))
    d1 = uuid.UUID(r["document_id"])
    await _run(w.data, d1)
    # Extraction would find nothing, but a verbatim document never goes through it.
    _facts(w.harness, [])
    short = f"ok {uuid.uuid4().hex[:6]}"
    d2 = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, sid, short, mode="verbatim"))[
            "document_id"
        ]
    )
    out = await _run(w.data, d2)

    assert out["memories_created"] == 1
    assert (await _doc(w.engine, w.data, d2))["meta"]["publication"] == "published"
    assert await _live(w.engine, w.data, d1) == 0 and await _live(w.engine, w.data, d2) == 1


# ── disconnect (C8) ──────────────────────────────────────────────────────────


async def test_dc1_disconnect_with_documents_queued_makes_no_provider_call(world) -> None:
    w = world
    retire = Retire()
    _install_retire(w.mp, retire)
    _facts(w.harness, [_fact()])
    embeds: list[int] = []
    original_embed = w.harness.embed

    async def counting_embed(facts: list[str]):
        embeds.append(len(facts))
        return await original_embed(facts)

    w.harness.embed = counting_embed  # type: ignore[method-assign]
    sid, d1, d2 = await _two_revisions(w)
    other = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"]
    )
    await _disconnect(w.engine, w.data, w.connector)

    for doc_id in (d1, d2, other):
        out = await _run(w.data, doc_id)
        assert out["status"] == "failed" and out["error_code"] == "connector_disconnected"
        doc = await _doc(w.engine, w.data, doc_id)
        assert doc["status"] == "failed"
        assert doc["meta"]["error_code"] == "connector_disconnected"
        assert await _total_memories(w.engine, w.data, doc_id) == 0

    assert w.harness.extract_calls == 0
    assert w.harness.fact_extraction_calls == 0
    assert embeds == []
    assert (await _head(w.engine, w.data, sid)).published_seq == 0
    assert retire.calls == []


async def test_connector_the_submitter_cannot_see_counts_as_not_active(world) -> None:
    w = world
    _facts(w.harness, [_fact()])
    ghost = uuid.uuid4()  # no such connector row
    r = await _handoff(w.engine, w.data, ghost, _sid(), _text())
    out = await _run(w.data, r["document_id"])
    assert out["error_code"] == "connector_disconnected"
    assert w.harness.fact_extraction_calls == 0


async def test_dc2_disconnect_racing_publication_gives_one_consistent_outcome(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact()])
    sid = _sid()
    d1 = uuid.UUID((await _handoff(w.engine, w.data, w.connector, sid, _text()))["document_id"])

    reached = asyncio.Event()
    release = asyncio.Event()
    original_embed = w.harness.embed

    async def gated_embed(facts: list[str]):
        results = await original_embed(facts)
        reached.set()
        await release.wait()
        return results

    w.harness.embed = gated_embed  # type: ignore[method-assign]
    task = asyncio.create_task(_run(w.data, d1))
    await asyncio.wait_for(reached.wait(), timeout=30)

    # A disconnect transaction holds the connector row while the worker is about
    # to publish; the worker's FOR SHARE must wait and then see the new state.
    disconnector = await _session(w.engine, w.data.owner_id, w.data.target_workspace_id)
    async with disconnector:
        await disconnector.execute(
            text(
                "UPDATE connector_configs SET generation = generation + 1, "
                "fence = fence + 1, status = 'disconnected' WHERE id = CAST(:c AS uuid)"
            ),
            {"c": str(w.connector)},
        )
        release.set()
        await asyncio.sleep(1.0)
        assert not task.done(), "publication must wait on the disconnect's row lock"
        await disconnector.commit()
    out = await asyncio.wait_for(task, timeout=30)

    assert out["status"] == "failed" and out["error_code"] == "connector_disconnected"
    assert await _total_memories(w.engine, w.data, d1) == 0
    assert (await _head(w.engine, w.data, sid)).published_seq == 0


async def test_publication_commits_before_a_later_disconnect(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact()])
    d1 = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"]
    )
    out = await _run(w.data, d1)
    await _disconnect(w.engine, w.data, w.connector)
    assert out["status"] == "completed"
    assert await _live(w.engine, w.data, d1) == 1  # removal is a separate, optional step (C8/C9)


# ── caps, usage, context ─────────────────────────────────────────────────────


async def test_caps_truncate_chunks_and_characters_and_record_it(world, monkeypatch) -> None:
    w = world
    monkeypatch.setenv("CONNECTOR_MAX_CHUNKS_PER_ARTIFACT", "2")
    monkeypatch.setenv("CONNECTOR_MAX_ARTIFACT_CHARS", "60")
    get_settings.cache_clear()
    from sourcemind.services.ingestion import chunker
    from sourcemind.services.ingestion.chunker import ChunkResult

    _install_retire(w.mp, _local_retire)
    seen_content: list[str] = []
    original_extract = w.harness.extract_document

    async def spy_extract(**kwargs: object):
        seen_content.append(str(kwargs["content"]))
        return await original_extract(**kwargs)

    from sourcemind.services.ingestion import extractor

    monkeypatch.setattr(extractor, "extract", spy_extract)

    async def five_chunks(_e: object) -> list[ChunkResult]:
        return [
            ChunkResult(content=f"c{i}", token_count=1, chunk_index=i, total_chunks=5,
                        chunk_type="text")
            for i in range(5)
        ]

    monkeypatch.setattr(chunker, "chunk", five_chunks)
    calls = _facts(w.harness, [_fact()], usage={"calls": 2, "input_tokens": 10,
                                                "output_tokens": 4, "estimated": True})
    content = "y" * 200
    d1 = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, _sid(), content))["document_id"]
    )
    out = await _run(w.data, d1)

    assert out["status"] == "completed"
    assert seen_content == ["y" * 60]
    assert len(calls[0]["chunks"]) == 2
    meta = (await _doc(w.engine, w.data, d1))["meta"]
    assert meta["truncated"] == {"chars": 200, "chunks": 5}
    assert meta["usage"] == {"calls": 2, "input_tokens": 10, "output_tokens": 4,
                             "estimated": True}


async def test_context_is_passed_separately_to_extraction(world) -> None:
    w = world
    _install_retire(w.mp, _local_retire)
    calls = _facts(w.harness, [_fact()])
    d1 = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"]
    )
    await _run(w.data, d1)
    assert calls[0]["context"] == "number: 7\nrepo: acme/repo"


# ── budget ───────────────────────────────────────────────────────────────────


async def test_budget_exhaustion_fails_document_pauses_connector_manual_unaffected(
    world, monkeypatch
) -> None:
    w = world
    monkeypatch.setenv("WORKSPACE_DAILY_EXTRACTION_BUDGET", "1")
    get_settings.cache_clear()
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact()])
    d1 = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"]
    )
    d2 = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"]
    )

    first = await _run(w.data, d1)
    second = await _run(w.data, d2)

    assert first["status"] == "completed"
    assert second["status"] == "failed" and second["error_code"] == "budget"
    assert w.harness.fact_extraction_calls == 1  # no provider call for the refused document
    meta = (await _doc(w.engine, w.data, d2))["meta"]
    assert meta["error_code"] == "budget" and meta["next_retry_at"]
    assert datetime.fromisoformat(meta["next_retry_at"]) > datetime.now(UTC)
    (status,) = await _rows(
        w.engine,
        w.data,
        "SELECT status FROM connector_configs WHERE id = CAST(:c AS uuid)",
        c=str(w.connector),
    )
    assert status.status == "paused_budget"

    # Manual ingestion is not budgeted.
    from sourcemind.workers.ingestion import _run_pipeline

    _job, manual_id = await _submit(w.engine, w.mp, w.data)
    manual = await _run_pipeline(
        FakeTask(retries=0),
        str(manual_id),
        str(w.data.target_workspace_id),
        str(w.data.member_id),
    )
    assert manual["status"] == "completed" and manual["memories_created"] == 1


# ── requeue rules ────────────────────────────────────────────────────────────


async def _failed_incomplete(w) -> uuid.UUID:
    _install_retire(w.mp, _local_retire)
    _facts(w.harness, [_fact()], failed=1)
    d = uuid.UUID((await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"])
    await _run(w.data, d)
    return d


async def _requeue(w, document_id) -> bool:
    from sourcemind.services.ingestion.receiver import requeue_transient_failure

    session = await _session(w.engine, w.data.member_id, w.data.target_workspace_id)
    async with session:
        return await requeue_transient_failure(
            session, document_id, w.data.target_workspace_id, now=datetime.now(UTC)
        )


async def _patch_meta(w, document_id, patch: dict[str, Any]) -> None:
    session = await _session(w.engine, w.data.owner_id, w.data.target_workspace_id)
    async with session:
        await session.execute(
            text(
                "UPDATE documents SET metadata = metadata || CAST(:p AS jsonb) "
                "WHERE id = CAST(:d AS uuid)"
            ),
            {"p": json.dumps(patch), "d": str(document_id)},
        )
        await session.commit()


async def test_requeue_refuses_ineligible_cases(world) -> None:
    w = world
    d = await _failed_incomplete(w)

    await _patch_meta(w, d, {"error_code": "budget"})
    assert await _requeue(w, d) is False
    await _patch_meta(w, d, {"error_code": "permanent"})
    assert await _requeue(w, d) is False

    await _patch_meta(w, d, {"error_code": "transient", "attempts": 3})
    assert await _requeue(w, d) is False  # connector_max_handoff_attempts reached
    assert (await _doc(w.engine, w.data, d))["status"] == "failed"

    await _patch_meta(w, d, {"attempts": 0})
    await _disconnect(w.engine, w.data, w.connector)
    assert await _requeue(w, d) is False  # connector no longer active
    assert (await _doc(w.engine, w.data, d))["status"] == "failed"


async def test_requeue_refuses_a_document_that_is_not_failed(world) -> None:
    w = world
    d = uuid.UUID((await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"])
    assert await _requeue(w, d) is False


async def test_requeue_resets_and_redispatches_with_backoff(world) -> None:
    w = world
    d = await _failed_incomplete(w)
    before = await _doc(w.engine, w.data, d)
    assert await _requeue(w, d) is True
    after = await _doc(w.engine, w.data, d)

    assert after["status"] == "pending"
    assert after["meta"]["attempts"] == before["meta"]["attempts"] + 1
    assert "error_code" not in after["meta"]
    assert datetime.fromisoformat(after["meta"]["next_retry_at"]) > datetime.now(UTC)
    assert after["meta"]["dispatch_state"] == "queued"
