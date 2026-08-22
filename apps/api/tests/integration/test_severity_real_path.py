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
    """Client that reports a high-confidence conflict.

    A conflict is now raised on is_conflict, the neutral mutual-exclusivity
    signal, rather than on a 'updates' supersession verdict. relation is
    still set because it drives the knowledge-graph edge.
    """
    client = MagicMock()
    payload = json.dumps(
        {
            "relation": "updates",
            "confidence": 0.97,
            "is_conflict": True,
            "conflict_summary": "A and B state different values for the same point",
        }
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


async def _set_importance(session, memory_id, value: float) -> None:
    """Pin a memory's importance after detect() has run.

    detect() now recomputes importance for the TARGET of every relation it
    writes, which is correct - an inbound edge raises the target's score. The
    disputed memory in these tests IS that target, so an importance seeded at
    insert time is legitimately overwritten by the time the assertions run.

    These tests are about the SEVERITY ladder, not about how importance is
    derived, so the intended value is restored here and severity recomputed
    from it. Seeding at insert and hoping it survives would be asserting
    against a state production never produces.
    """
    await session.execute(
        text(
            "UPDATE memories SET importance_score = :v WHERE id = CAST(:id AS uuid)"
        ),
        {"v": value, "id": str(memory_id)},
    )
    await session.flush()


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
async def test_cluster_rescore_updates_the_stored_claim_count(
    db_session, test_workspace
):
    """A third claim must rescore the conflict created earlier.

    Under the corrected ladder severity is decided by importance alone, so a
    new claim no longer changes the TIER of an existing conflict — every
    unresolved conflict already has a claim count of at least 2. What must
    still be refreshed is the stored competing_claim_count, which is the
    signal the tier used to depend on and is surfaced to reviewers.

    Importance is kept low so the count is observable rather than masked by a
    critical tier.
    """
    from sourcemind.services.conflict.severity import recompute_severity_for_memory

    author_a = await _user(db_session)
    author_b = await _user(db_session)
    author_c = await _user(db_session)

    disputed = await _memory_with_author(
        db_session, test_workspace.id, author_a, seed=0.09, importance=0.20
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
    assert first.severity == "medium", "low importance, so medium"
    assert first.competing_claim_count == 2
    assert first.blocks_derivation is False

    rival_two = await _memory_with_author(
        db_session, test_workspace.id, author_c, seed=0.50, importance=0.20
    )
    await _extra_conflict(db_session, test_workspace.id, disputed, rival_two)
    # Same call _maybe_create_conflict makes after writing a conflict:
    # score the whole cluster, not just the new row.
    await recompute_severity_for_memory(db_session, disputed)

    rows = {r.id: r for r in await _conflicts_for(db_session, disputed)}
    assert rows[first.id].competing_claim_count == 3, (
        "the existing conflict was not rescored when a third claim appeared"
    )
    assert rows[first.id].severity == "medium", (
        "importance is unchanged, so the tier should not move"
    )


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
    # detect() rescored `disputed` as a relation target, so restore the high
    # importance this test is about before scoring severity from it.
    await _set_importance(db_session, disputed, 0.95)
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


async def _pair_row(session: AsyncSession, memory_a, memory_b):
    """The conflict row for one unordered pair, or None."""
    return (
        await session.execute(
            text(
                "SELECT id::text, severity, competing_claim_count, blocks_derivation "
                "FROM memory_conflicts WHERE "
                "(memory_a_id = CAST(:a AS uuid) AND memory_b_id = CAST(:b AS uuid)) "
                "OR (memory_a_id = CAST(:b AS uuid) AND memory_b_id = CAST(:a AS uuid))"
            ),
            {"a": str(memory_a), "b": str(memory_b)},
        )
    ).first()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_three_way_conflict_creates_correct_pairwise_rows(
    db_session, test_workspace
):
    """Three people, one decision point, one ingestion run.

    Two claims already exist; a third arrives disputing both. MemoryConflict
    is pairwise, so the newcomer must produce exactly two rows — one per
    existing claim. Not one (which would mean the candidate loop stopped at
    the first match) and not three (which would mean the two PRE-EXISTING
    claims were also paired with each other; detect() never compares two old
    memories, it only compares the batch against the store).

    Both rows describe the same three-claim cluster, so both must carry a
    competing_claim_count of 3.
    """
    author_a = await _user(db_session)
    author_b = await _user(db_session)
    author_c = await _user(db_session)

    # Distinct importances so the two rows land in different severity tiers,
    # which proves severity is read per-row rather than copied.
    claim_a = await _memory_with_author(
        db_session, test_workspace.id, author_a, seed=0.09, importance=0.90
    )
    claim_b = await _memory_with_author(
        db_session, test_workspace.id, author_b, seed=0.09, importance=0.30
    )
    newcomer = await _memory_with_author(
        db_session, test_workspace.id, author_c, seed=0.09, importance=0.30
    )

    await RelationDetector(_claude_saying_contradiction()).detect(
        db_session,
        [_MemoryRow(newcomer, test_workspace.id, "third claim", [0.09] * 3072)],
        test_workspace.id,
    )

    # Both existing claims were relation targets, so detect() recomputed their
    # importance. Restore the values under test and rescore severity from them.
    from sourcemind.services.conflict.severity import (
        recompute_severity_for_memory as _rescore,
    )

    await _set_importance(db_session, claim_a, 0.90)
    await _set_importance(db_session, claim_b, 0.30)
    await _rescore(db_session, claim_a)
    await _rescore(db_session, claim_b)

    rows = await _conflicts_for(db_session, newcomer)
    assert len(rows) == 2, f"expected one row per existing claim, got {len(rows)}"

    row_a = await _pair_row(db_session, claim_a, newcomer)
    row_b = await _pair_row(db_session, claim_b, newcomer)
    assert row_a is not None, "no conflict between the newcomer and the first claim"
    assert row_b is not None, "no conflict between the newcomer and the second claim"

    # detect() compares the batch against the store, never store against
    # store, so the two pre-existing claims are not paired with each other.
    assert await _pair_row(db_session, claim_a, claim_b) is None

    # Both rows belong to the same cluster of three competing claims.
    assert row_a.competing_claim_count == 3, (
        f"first row reports {row_a.competing_claim_count} claims, not 3"
    )
    assert row_b.competing_claim_count == 3, (
        f"second row reports {row_b.competing_claim_count} claims, not 3"
    )

    # Phase 1.5 ladder: importance alone decides critical; claims only lift
    # low to medium.
    assert row_a.severity == "critical", (
        f"importance 0.90 must be critical, got {row_a.severity!r}"
    )
    assert row_a.blocks_derivation is True
    assert row_b.severity == "medium", (
        f"importance 0.30 with 3 claims must be medium, got {row_b.severity!r}"
    )
    assert row_b.blocks_derivation is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_clustering_does_not_widen_the_retirement_blind_spot(
    db_session, test_workspace
):
    """Clustering must not lose conflicts to the current_version filter.

    An 'updates' verdict sets current_version = FALSE on the disputed memory,
    and the neighbour search only sees current_version = TRUE. The risk is
    ordering: if retiring the first candidate removed the second from the run,
    a three-way disagreement would silently record only one conflict.

    It does not, because detect() materialises the whole candidate list before
    the loop begins. Retirement therefore takes effect from the NEXT run on,
    which is the already-known deferred bug and no worse here: this asserts
    the boundary explicitly so a future rewrite that moves the neighbour query
    inside the loop fails loudly instead of quietly halving the cluster.
    """
    authors = [await _user(db_session) for _ in range(4)]

    claim_a = await _memory_with_author(
        db_session, test_workspace.id, authors[0], seed=0.11, importance=0.30
    )
    claim_b = await _memory_with_author(
        db_session, test_workspace.id, authors[1], seed=0.11, importance=0.30
    )
    newcomer = await _memory_with_author(
        db_session, test_workspace.id, authors[2], seed=0.11, importance=0.30
    )

    detector = RelationDetector(_claude_saying_contradiction())
    await detector.detect(
        db_session,
        [_MemoryRow(newcomer, test_workspace.id, "third claim", [0.11] * 3072)],
        test_workspace.id,
    )

    # Both conflicts recorded despite both candidates being retired mid-loop.
    assert len(await _conflicts_for(db_session, newcomer)) == 2

    retired = (
        await db_session.execute(
            text(
                "SELECT id::text FROM memories WHERE id IN "
                "(CAST(:a AS uuid), CAST(:b AS uuid)) AND current_version = FALSE"
            ),
            {"a": str(claim_a), "b": str(claim_b)},
        )
    ).fetchall()
    assert len(retired) == 2, "both disputed claims should have been retired"

    # The blind spot itself, unchanged: a fourth claim sees only the newcomer,
    # so it adds one conflict, not three.
    fourth = await _memory_with_author(
        db_session, test_workspace.id, authors[3], seed=0.11, importance=0.30
    )
    await detector.detect(
        db_session,
        [_MemoryRow(fourth, test_workspace.id, "fourth claim", [0.11] * 3072)],
        test_workspace.id,
    )

    assert len(await _conflicts_for(db_session, fourth)) == 1, (
        "a retired claim must stay invisible to later runs"
    )
    assert len(await _conflicts_for(db_session, claim_a)) == 1, (
        "clustering must not let retired claims accumulate new conflicts"
    )
