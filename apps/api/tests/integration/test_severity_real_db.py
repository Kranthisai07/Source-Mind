"""Real-database test for conflict severity.

Mocked tests cannot catch what broke _find_successor: a query that is correct
in isolation but unplannable by Postgres. compute_conflict_severity writes to
three columns added by migration 0005 and counts across memory_conflicts, so
column names, the CHECK constraint and the claim-count SQL all have to be
exercised against a real server.

Everything runs inside an outer transaction that is rolled back, so nothing
persists.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.services.conflict.severity import compute_conflict_severity


async def _memory(session: AsyncSession, workspace_id, importance: float) -> uuid.UUID:
    """Insert a memory with a known importance_score."""
    memory_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memories (id, workspace_id, content, content_hash, "
            "version, current_version, importance_score) "
            "VALUES (CAST(:id AS uuid), CAST(:ws AS uuid), :content, :hash, "
            "1, TRUE, :imp)"
        ),
        {
            "id": str(memory_id),
            "ws": str(workspace_id),
            "content": f"claim {memory_id}",
            "hash": uuid.uuid4().hex,
            "imp": importance,
        },
    )
    return memory_id


async def _conflict(session: AsyncSession, workspace_id, a, b) -> uuid.UUID:
    conflict_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memory_conflicts (id, workspace_id, memory_a_id, "
            "memory_b_id, conflict_type, status, similarity_score) "
            "VALUES (CAST(:id AS uuid), CAST(:ws AS uuid), CAST(:a AS uuid), "
            "CAST(:b AS uuid), 'contradiction', 'open', 0.9)"
        ),
        {"id": str(conflict_id), "ws": str(workspace_id), "a": str(a), "b": str(b)},
    )
    return conflict_id


async def _read(session: AsyncSession, conflict_id):
    return (
        await session.execute(
            text(
                "SELECT severity, competing_claim_count, blocks_derivation "
                "FROM memory_conflicts WHERE id = CAST(:cid AS uuid)"
            ),
            {"cid": str(conflict_id)},
        )
    ).first()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_severity_persists_against_real_postgres(db_session, test_workspace):
    """One pairwise conflict on a low-importance memory scores medium.

    Claim count is 2 — the disputing memory plus the disputed one — which
    satisfies the medium clause on its own.
    """
    disputed = await _memory(db_session, test_workspace.id, importance=0.2)
    other = await _memory(db_session, test_workspace.id, importance=0.2)
    conflict_id = await _conflict(db_session, test_workspace.id, disputed, other)

    severity = await compute_conflict_severity(db_session, conflict_id)
    assert severity == "medium"

    row = await _read(db_session, conflict_id)
    assert row.severity == "medium"
    assert row.competing_claim_count == 2
    assert row.blocks_derivation is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_critical_requires_high_importance_and_three_claims(
    db_session, test_workspace
):
    """Two separate memories disputing one important memory -> critical.

    This is the case the pairwise model could not express before
    competing_claim_count was derived across conflict rows.
    """
    disputed = await _memory(db_session, test_workspace.id, importance=0.85)
    rival_one = await _memory(db_session, test_workspace.id, importance=0.3)
    rival_two = await _memory(db_session, test_workspace.id, importance=0.3)

    first = await _conflict(db_session, test_workspace.id, disputed, rival_one)
    await _conflict(db_session, test_workspace.id, disputed, rival_two)

    severity = await compute_conflict_severity(db_session, first)
    assert severity == "critical"

    row = await _read(db_session, first)
    assert row.competing_claim_count == 3, "two rivals plus the disputed memory"
    assert row.blocks_derivation is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_high_importance_with_a_single_rival_is_only_medium(
    db_session, test_workspace
):
    """The critical clause is AND, not OR — importance alone is not enough."""
    disputed = await _memory(db_session, test_workspace.id, importance=0.95)
    rival = await _memory(db_session, test_workspace.id, importance=0.1)
    conflict_id = await _conflict(db_session, test_workspace.id, disputed, rival)

    assert await compute_conflict_severity(db_session, conflict_id) == "medium"
    row = await _read(db_session, conflict_id)
    assert row.competing_claim_count == 2
    assert row.blocks_derivation is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resolved_conflicts_are_excluded_from_the_claim_count(
    db_session, test_workspace
):
    """A settled disagreement is no longer a competing claim.

    Same shape as the critical case, but one rival conflict is resolved, so
    the count drops to 2 and severity falls back to medium.
    """
    disputed = await _memory(db_session, test_workspace.id, importance=0.85)
    rival_one = await _memory(db_session, test_workspace.id, importance=0.3)
    rival_two = await _memory(db_session, test_workspace.id, importance=0.3)

    first = await _conflict(db_session, test_workspace.id, disputed, rival_one)
    second = await _conflict(db_session, test_workspace.id, disputed, rival_two)
    await db_session.execute(
        text(
            "UPDATE memory_conflicts SET status = 'resolved' "
            "WHERE id = CAST(:cid AS uuid)"
        ),
        {"cid": str(second)},
    )

    assert await compute_conflict_severity(db_session, first) == "medium"
    row = await _read(db_session, first)
    assert row.competing_claim_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_check_constraint_rejects_an_unknown_severity(
    db_session, test_workspace
):
    """Migration 0005's CHECK must actually be enforced by the server."""
    from sqlalchemy.exc import IntegrityError

    a = await _memory(db_session, test_workspace.id, importance=0.5)
    b = await _memory(db_session, test_workspace.id, importance=0.5)
    conflict_id = await _conflict(db_session, test_workspace.id, a, b)

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "UPDATE memory_conflicts SET severity = 'catastrophic' "
                "WHERE id = CAST(:cid AS uuid)"
            ),
            {"cid": str(conflict_id)},
        )
        await db_session.flush()
