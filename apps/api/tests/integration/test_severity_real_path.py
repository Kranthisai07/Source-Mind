"""Conflict severity through the REAL ingestion path, against live Postgres.

WHY THIS EXISTS
---------------
Phase 1 hooked the severity rescore into detect_relations(), a module-level
wrapper whose docstring claimed the Celery task used it. It did not:
workers/ingestion.py calls RelationDetector.detect() directly, so the hook sat
on a path with zero callers and freshly ingested memories were never rescored.

These tests therefore call **RelationDetector.detect()** — the exact function
workers/ingestion.py:195 invokes — and never any wrapper or helper. If the
post-processing is ever moved back out of detect(), they fail.

SCOPE, STATED HONESTLY
----------------------
The Anthropic client is mocked. Conflict creation requires Claude to classify
a pair as "updates" with confidence >= 0.85, which is non-deterministic and
costs money; mocking it makes the *wiring* deterministic. Everything else is
real: real Postgres, real pgvector neighbour search, real inserts, real
severity SQL, real rows read back. This is not a full Celery run — the e2e
test covers that — it is the real code path with one boundary stubbed.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.services.memory.relations import RelationDetector


def _claude_saying_contradiction():
    """Client whose classification always yields a high-confidence 'updates'.

    'updates' is the only verdict that produces a MemoryConflict, and it must
    clear _MIN_CONFIDENCE (0.85).
    """
    client = MagicMock()
    payload = json.dumps(
        {"relation": "updates", "confidence": 0.97, "reasoning": "direct contradiction"}
    )
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=payload)])
    )
    return client


def _vector(seed: float) -> str:
    """A pgvector literal. Nearly-identical vectors sit inside _CONFLICT_RADIUS."""
    return "[" + ",".join([f"{seed:.6f}"] * 3072) + "]"


async def _user(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:10]
    await session.execute(
        text(
            "INSERT INTO users (id, clerk_id, email, display_name) VALUES "
            "(CAST(:id AS uuid), :clerk, :email, 'Test User')"
        ),
        {"id": str(user_id), "clerk": f"clerk-{suffix}", "email": f"{suffix}@t.local"},
    )
    return user_id


async def _memory_with_author(
    session: AsyncSession, workspace_id, author_id, *, seed: float, importance: float
):
    """A memory with an embedding and an attribution.

    Conflict detection requires both: the neighbour search needs the vector,
    and _maybe_create_conflict bails unless both memories have an attributed
    contributor and those contributors differ.
    """
    memory_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memories (id, workspace_id, content, content_hash, version, "
            "current_version, importance_score, embedding) VALUES "
            "(CAST(:id AS uuid), CAST(:ws AS uuid), :content, :hash, 1, TRUE, :imp, "
            "CAST(:emb AS vector))"
        ),
        {
            "id": str(memory_id),
            "ws": str(workspace_id),
            "content": f"claim {memory_id}",
            "hash": uuid.uuid4().hex,
            "imp": importance,
            "emb": _vector(seed),
        },
    )
    await session.execute(
        text(
            "INSERT INTO attributions (memory_id, user_id, contribution_weight, "
            "trigger_action) VALUES (CAST(:mid AS uuid), CAST(:uid AS uuid), 1.0, 'create')"
        ),
        {"mid": str(memory_id), "uid": str(author_id)},
    )
    await session.flush()
    return memory_id


class _MemoryRow:
    """Minimal stand-in for the ORM Memory objects detect() receives.

    workers/ingestion.py passes real Memory instances returned by
    store_memories; detect() only reads .id, .content, .embedding and
    .workspace_id from them.
    """

    def __init__(self, memory_id, workspace_id, content, embedding):
        self.id = memory_id
        self.workspace_id = workspace_id
        self.content = content
        self.embedding = embedding


async def _conflicts_for(session: AsyncSession, memory_id):
    rows = (
        await session.execute(
            text(
                "SELECT id::text, severity, competing_claim_count, blocks_derivation, "
                "status FROM memory_conflicts WHERE memory_a_id = CAST(:mid AS uuid) "
                "OR memory_b_id = CAST(:mid AS uuid) ORDER BY created_at"
            ),
            {"mid": str(memory_id)},
        )
    ).fetchall()
    return rows


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_detect_path_computes_severity_without_any_wrapper(
    db_session, test_workspace
):
    """One rival through detect() -> conflict exists and is scored.

    Nothing here calls compute_conflict_severity or any wrapper. If detect()
    stops doing the post-processing, severity stays at the column default and
    competing_claim_count never leaves 2 — which is what this asserts against.
    """
    author_a = await _user(db_session)
    author_b = await _user(db_session)

    disputed = await _memory_with_author(
        db_session, test_workspace.id, author_a, seed=0.05, importance=0.85
    )
    rival = await _memory_with_author(
        db_session, test_workspace.id, author_b, seed=0.05, importance=0.30
    )

    # The exact call workers/ingestion.py makes.
    await RelationDetector(_claude_saying_contradiction()).detect(
        db_session,
        [_MemoryRow(rival, test_workspace.id, "rival claim", [0.05] * 3072)],
        test_workspace.id,
    )

    rows = await _conflicts_for(db_session, disputed)
    assert rows, "no conflict was created through the real detect() path"
    row = rows[0]
    assert row.severity in ("medium", "critical"), (
        f"severity was never computed (got {row.severity!r})"
    )
    assert row.competing_claim_count >= 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_updates_verdict_retires_the_disputed_memory(
    db_session, test_workspace
):
    """A second rival cannot reach the same memory through detect().

    When Claude classifies a pair as 'updates', detect() sets
    current_version = FALSE on the disputed memory. The neighbour search
    filters on current_version = TRUE, so that memory is invisible to every
    later detect() call and can never collect a second conflict this way.

    Consequence worth stating plainly: the critical tier, which needs three
    competing claims, is effectively unreachable through ingestion alone. It
    is reachable when conflicts arrive from separate sources — a re-run where
    the relation insert failed, or conflicts created outside this path.
    """
    author_a = await _user(db_session)
    author_b = await _user(db_session)
    author_c = await _user(db_session)

    disputed = await _memory_with_author(
        db_session, test_workspace.id, author_a, seed=0.07, importance=0.90
    )
    rival_one = await _memory_with_author(
        db_session, test_workspace.id, author_b, seed=0.07, importance=0.20
    )

    detector = RelationDetector(_claude_saying_contradiction())
    await detector.detect(
        db_session,
        [_MemoryRow(rival_one, test_workspace.id, "first rival", [0.07] * 3072)],
        test_workspace.id,
    )

    assert len(await _conflicts_for(db_session, disputed)) == 1

    still_current = (
        await db_session.execute(
            text("SELECT current_version FROM memories WHERE id = CAST(:id AS uuid)"),
            {"id": str(disputed)},
        )
    ).scalar()
    assert still_current is False, "an 'updates' verdict should retire the memory"

    rival_two = await _memory_with_author(
        db_session, test_workspace.id, author_c, seed=0.07, importance=0.20
    )
    await detector.detect(
        db_session,
        [_MemoryRow(rival_two, test_workspace.id, "second rival", [0.07] * 3072)],
        test_workspace.id,
    )

    assert len(await _conflicts_for(db_session, disputed)) == 1, (
        "a retired memory must not accumulate further conflicts"
    )


async def _extra_conflict(session, workspace_id, memory_a, memory_b):
    """A conflict arriving from a source other than this detect() run.

    Realistic because a retired memory is invisible to detect(): further
    conflicts on it can only come from a re-run where the relation insert
    failed, or from outside the ingestion path.
    """
    conflict_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memory_conflicts (id, workspace_id, memory_a_id, "
            "memory_b_id, conflict_type, status, similarity_score) VALUES "
            "(CAST(:id AS uuid), CAST(:ws AS uuid), CAST(:a AS uuid), "
            "CAST(:b AS uuid), 'contradiction', 'open', 0.9)"
        ),
        {
            "id": str(conflict_id),
            "ws": str(workspace_id),
            "a": str(memory_a),
            "b": str(memory_b),
        },
    )
    return conflict_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cluster_rescore_lifts_an_existing_conflict_to_critical(
    db_session, test_workspace
):
    """A third claim must rescore the conflict created earlier.

    The first conflict comes through the real detect() path and scores medium.
    A second conflict on the same memory takes the count to 3, which must lift
    the FIRST one to critical rather than leaving it at its creation score.
    """
    from sourcemind.services.conflict.severity import recompute_severity_for_memory

    author_a = await _user(db_session)
    author_b = await _user(db_session)
    author_c = await _user(db_session)

    disputed = await _memory_with_author(
        db_session, test_workspace.id, author_a, seed=0.09, importance=0.95
    )
    rival_one = await _memory_with_author(
        db_session, test_workspace.id, author_b, seed=0.09, importance=0.20
    )

    await RelationDetector(_claude_saying_contradiction()).detect(
        db_session,
        [_MemoryRow(rival_one, test_workspace.id, "first rival", [0.09] * 3072)],
        test_workspace.id,
    )

    first = (await _conflicts_for(db_session, disputed))[0]
    assert first.severity == "medium", "one rival is not yet critical"
    assert first.blocks_derivation is False

    rival_two = await _memory_with_author(
        db_session, test_workspace.id, author_c, seed=0.50, importance=0.20
    )
    await _extra_conflict(db_session, test_workspace.id, disputed, rival_two)
    # Same call _maybe_create_conflict makes after writing a conflict:
    # score the whole cluster, not just the new row.
    await recompute_severity_for_memory(db_session, disputed)

    rows = {r.id: r for r in await _conflicts_for(db_session, disputed)}
    assert rows[first.id].severity == "critical", (
        "the existing conflict was not rescored when a third claim appeared"
    )
    assert rows[first.id].competing_claim_count == 3
    assert rows[first.id].blocks_derivation is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_blocks_derivation_clears_when_the_conflict_is_resolved(
    db_session, test_workspace
):
    """blocks_derivation must SET on the real path and CLEAR on resolution.

    Resolution goes through resolve_conflict(), the same function the API
    calls, rather than a direct UPDATE.
    """
    from sourcemind.services.conflict.resolver import resolve_conflict
    from sourcemind.services.conflict.severity import recompute_severity_for_memory

    author_a = await _user(db_session)
    author_b = await _user(db_session)
    author_c = await _user(db_session)

    disputed = await _memory_with_author(
        db_session, test_workspace.id, author_a, seed=0.11, importance=0.95
    )
    rival_one = await _memory_with_author(
        db_session, test_workspace.id, author_b, seed=0.11, importance=0.20
    )

    await RelationDetector(_claude_saying_contradiction()).detect(
        db_session,
        [_MemoryRow(rival_one, test_workspace.id, "first rival", [0.11] * 3072)],
        test_workspace.id,
    )
    target = (await _conflicts_for(db_session, disputed))[0]

    rival_two = await _memory_with_author(
        db_session, test_workspace.id, author_c, seed=0.60, importance=0.20
    )
    await _extra_conflict(db_session, test_workspace.id, disputed, rival_two)
    # Same call _maybe_create_conflict makes after writing a conflict:
    # score the whole cluster, not just the new row.
    await recompute_severity_for_memory(db_session, disputed)

    before = {r.id: r for r in await _conflicts_for(db_session, disputed)}[target.id]
    assert before.blocks_derivation is True, "expected a blocking conflict"

    await resolve_conflict(
        session=db_session,
        conflict_id=uuid.UUID(target.id),
        resolver_id=author_a,
        resolution_type="kept_a",
        resolution_note="picked the original",
    )

    row = (
        await db_session.execute(
            text(
                "SELECT status, severity, blocks_derivation FROM memory_conflicts "
                "WHERE id = CAST(:cid AS uuid)"
            ),
            {"cid": target.id},
        )
    ).first()
    assert row.status == "resolved"
    assert row.blocks_derivation is False, (
        "a resolved conflict must stop blocking derivation"
    )
    assert row.severity == "critical", (
        "severity is retained as a record of how serious it was"
    )
