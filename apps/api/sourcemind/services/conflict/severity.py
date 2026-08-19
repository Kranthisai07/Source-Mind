"""Conflict severity computation.

Severity is DERIVED, never stored by hand. It combines two live inputs:

  * the disputed memory's importance_score, already maintained by
    services/memory/importance.py — read here, never recomputed
  * competing_claim_count: how many distinct memories dispute the same memory

Ladder (ADR — conflict severity):

    critical  importance_score > 0.7
    medium    importance_score > 0.4 OR competing_claim_count >= 2
    low       everything else

Claim count is a secondary signal only: it can lift something from low to
medium, but it is never a REQUIREMENT for critical. Critical previously also
demanded three competing claims, which made it unreachable for the ordinary
case — two people disagreeing about an important decision — and, given that
an 'updates' verdict retires the disputed memory, very nearly unreachable
through ingestion at all.

NO AUTO-RESOLUTION AT ANY SEVERITY, EVER. Severity ranks attention; it never
decides an outcome. `blocks_derivation` is the strongest effect available and
only marks a conflict as blocking — a human still resolves it. See ADR-008 on
AI being advisory-only for conflicts.

WHY competing_claim_count IS DERIVED
------------------------------------
MemoryConflict is strictly pairwise (memory_a_id, memory_b_id), so a single
conflict row is always exactly 2 claims. Counting per-row would pin every
conflict at 2 and make the critical tier unreachable. Instead the count is the
number of distinct memories disputing the disputed memory across ALL of its
conflict rows, plus the memory itself. Two separate memories contradicting one
memory yields 3, which is the situation the critical tier is meant to catch.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# Thresholds
_CRITICAL_IMPORTANCE = 0.7
_MEDIUM_IMPORTANCE = 0.4
_MEDIUM_CLAIMS = 2

SEVERITY_CRITICAL = "critical"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


def classify_severity(importance_score: float, competing_claim_count: int) -> str:
    """Pure severity ladder. Separated so the thresholds are testable alone."""
    # Importance alone decides critical. Two people disagreeing about a
    # high-importance decision is already critical; requiring a third claim
    # only delayed the signal.
    if importance_score > _CRITICAL_IMPORTANCE:
        return SEVERITY_CRITICAL
    if (
        importance_score > _MEDIUM_IMPORTANCE
        or competing_claim_count >= _MEDIUM_CLAIMS
    ):
        return SEVERITY_MEDIUM
    return SEVERITY_LOW


async def compute_conflict_severity(
    session: AsyncSession, conflict_id: uuid.UUID
) -> str:
    """Score one conflict and persist severity + blocks_derivation.

    Returns the severity written, or "" if the conflict does not exist.

    Reads importance_score as-is. Recomputing it here would make severity
    computation a write path into an unrelated table and could recurse, since
    recompute_importance is itself one of the triggers for this function.
    """
    row = (
        await session.execute(
            text(
                "SELECT memory_a_id::text, memory_b_id::text, status "
                "FROM memory_conflicts WHERE id = CAST(:cid AS uuid)"
            ),
            {"cid": str(conflict_id)},
        )
    ).first()
    if row is None:
        log.warning("severity.conflict_not_found", conflict_id=str(conflict_id))
        return ""

    memory_a_id, memory_b_id, status = row

    # The disputed memory is memory_a: detection writes the pre-existing
    # candidate as memory_a and the new contradicting memory as memory_b, so
    # memory_a is the claim being challenged.
    importance = (
        await session.execute(
            text(
                "SELECT COALESCE(importance_score, 0.0) FROM memories "
                "WHERE id = CAST(:mid AS uuid)"
            ),
            {"mid": memory_a_id},
        )
    ).scalar()
    importance = float(importance or 0.0)

    # Distinct memories disputing memory_a, across every conflict it appears
    # in, plus memory_a itself. Resolved conflicts are excluded: a settled
    # disagreement is not a competing claim any more.
    disputing = (
        await session.execute(
            text(
                "SELECT COUNT(DISTINCT other) FROM ("
                "  SELECT CASE WHEN memory_a_id = CAST(:mid AS uuid) "
                "              THEN memory_b_id ELSE memory_a_id END AS other"
                "  FROM memory_conflicts"
                "  WHERE (memory_a_id = CAST(:mid AS uuid) "
                "         OR memory_b_id = CAST(:mid AS uuid))"
                "    AND status NOT IN ('resolved', 'deferred')"
                ") d"
            ),
            {"mid": memory_a_id},
        )
    ).scalar()
    competing_claim_count = int(disputing or 0) + 1

    severity = classify_severity(importance, competing_claim_count)
    # Blocking is meaningless once the conflict is settled.
    blocks = severity == SEVERITY_CRITICAL and status not in ("resolved", "deferred")

    await session.execute(
        text(
            "UPDATE memory_conflicts SET severity = :sev, "
            "competing_claim_count = :cnt, blocks_derivation = :blk "
            "WHERE id = CAST(:cid AS uuid)"
        ),
        {
            "sev": severity,
            "cnt": competing_claim_count,
            "blk": blocks,
            "cid": str(conflict_id),
        },
    )
    await session.flush()

    log.info(
        "severity.computed",
        conflict_id=str(conflict_id),
        severity=severity,
        importance=round(importance, 4),
        competing_claims=competing_claim_count,
        blocks_derivation=blocks,
    )
    return severity


async def recompute_severity_for_memory(
    session: AsyncSession, memory_id: uuid.UUID
) -> int:
    """Rescore every unresolved conflict touching a memory.

    Called wherever importance_score changes, so severity cannot go stale
    behind it. Returns the number of conflicts rescored.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id::text FROM memory_conflicts "
                "WHERE (memory_a_id = CAST(:mid AS uuid) "
                "       OR memory_b_id = CAST(:mid AS uuid)) "
                "  AND status NOT IN ('resolved', 'deferred')"
            ),
            {"mid": str(memory_id)},
        )
    ).fetchall()

    for (conflict_id,) in rows:
        await compute_conflict_severity(session, uuid.UUID(conflict_id))
    return len(rows)
