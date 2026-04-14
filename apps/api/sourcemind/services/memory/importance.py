"""
Memory importance score computation.

The importance score is a [0.0–1.0] float that reflects how critical a memory
is to the workspace knowledge base. It is recomputed whenever significant
signals change: edits, new relations, conflict resolution, or handoff assignment.

Signals and weights:
  S1 inbound_relations  0.35  — how many other memories reference this one
  S2 approval_count     0.25  — number of explicit approvals in attribution
  S3 version_count      0.20  — how many times this memory has been edited
  S4 recency            0.10  — days since last update (decays over 180 days)
  S5 category_weight    0.10  — decision/process memories are more important

See ADR-007 for design rationale.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import UUID

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.models.memory import Memory

log = structlog.get_logger(__name__)

# Signal weights
_W1 = 0.35  # inbound relations
_W2 = 0.25  # approval count
_W3 = 0.20  # version count
_W4 = 0.10  # recency
_W5 = 0.10  # category weight

# Category importance weights (higher = more important)
_CATEGORY_WEIGHTS: dict[str, float] = {
    "decision": 1.0,
    "process": 0.9,
    "definition": 0.7,
    "fact": 0.5,
    "question": 0.3,
}

# Recency decay: half-life of 90 days, fully decays at ~180 days
_RECENCY_HALF_LIFE_DAYS = 90.0


def _s1_inbound_relations(inbound_count: int) -> float:
    """Logarithmic scale: each doubling adds equal importance."""
    if inbound_count <= 0:
        return 0.0
    return min(1.0, math.log2(inbound_count + 1) / math.log2(17))  # saturates at 16 inbound


def _s2_approval_count(approval_count: int) -> float:
    """Linear up to 5 approvals, then saturates."""
    return min(1.0, approval_count / 5.0)


def _s3_version_count(version_count: int) -> float:
    """Logarithmic: more edits = more refined = more important."""
    if version_count <= 1:
        return 0.1  # baseline for any memory
    return min(1.0, math.log2(version_count) / math.log2(8))  # saturates at 8 versions


def _s4_recency(last_updated_at: datetime) -> float:
    """Exponential decay based on days since last update."""
    now = datetime.now(timezone.utc)
    if last_updated_at.tzinfo is None:
        last_updated_at = last_updated_at.replace(tzinfo=timezone.utc)
    age_days = (now - last_updated_at).total_seconds() / 86400.0
    return math.exp(-age_days / _RECENCY_HALF_LIFE_DAYS)


def _s5_category(category: str | None) -> float:
    """Return weight based on memory category."""
    if not category:
        return 0.5  # neutral default
    return _CATEGORY_WEIGHTS.get(category.lower(), 0.5)


def compute_importance_score(
    inbound_count: int,
    approval_count: int,
    version_count: int,
    last_updated_at: datetime,
    category: str | None,
) -> float:
    """
    Compute the importance score from pre-fetched signal inputs.

    Returns a float in [0.0–1.0].
    """
    s1 = _s1_inbound_relations(inbound_count)
    s2 = _s2_approval_count(approval_count)
    s3 = _s3_version_count(version_count)
    s4 = _s4_recency(last_updated_at)
    s5 = _s5_category(category)

    score = s1 * _W1 + s2 * _W2 + s3 * _W3 + s4 * _W4 + s5 * _W5
    return round(min(1.0, max(0.0, score)), 6)


async def recompute_importance(session: AsyncSession, memory_id: UUID) -> float:
    """
    Recompute and persist the importance score for one memory.

    Fetches all required signals from DB in a single round-trip,
    computes the score, and writes it back to the memory row.

    Returns the new score.
    """
    # Fetch memory + signals in one query
    result = await session.execute(
        text("""
            SELECT
                m.id,
                m.version,
                m.category,
                m.updated_at,
                m.created_at,
                COALESCE(ir.inbound_count, 0)  AS inbound_count,
                COALESCE(ap.approval_count, 0) AS approval_count,
                COALESCE(vc.version_count, 1)  AS version_count
            FROM memories m
            LEFT JOIN (
                SELECT target_memory_id, COUNT(*) AS inbound_count
                FROM memory_relations
                WHERE target_memory_id = :mid::uuid
                GROUP BY target_memory_id
            ) ir ON ir.target_memory_id = m.id
            LEFT JOIN (
                SELECT memory_id, COUNT(*) AS approval_count
                FROM attribution_records
                WHERE memory_id = :mid::uuid
                  AND action_type IN ('approved', 'merged', 'accepted')
                GROUP BY memory_id
            ) ap ON ap.memory_id = m.id
            LEFT JOIN (
                SELECT parent_memory_id, COUNT(*) AS version_count
                FROM memories
                WHERE parent_memory_id = :mid::uuid
                   OR id = :mid::uuid
                GROUP BY parent_memory_id
            ) vc ON TRUE
            WHERE m.id = :mid::uuid
        """),
        {"mid": str(memory_id)},
    )
    row = result.fetchone()

    if row is None:
        log.warning("importance.memory_not_found", memory_id=str(memory_id))
        return 0.0

    last_updated = row.updated_at or row.created_at
    score = compute_importance_score(
        inbound_count=row.inbound_count,
        approval_count=row.approval_count,
        version_count=row.version_count,
        last_updated_at=last_updated,
        category=row.category,
    )

    # Write back
    await session.execute(
        text("UPDATE memories SET importance_score = :score WHERE id = :mid::uuid"),
        {"score": score, "mid": str(memory_id)},
    )

    log.debug(
        "importance.recomputed",
        memory_id=str(memory_id),
        score=score,
        inbound=row.inbound_count,
        approvals=row.approval_count,
        versions=row.version_count,
    )

    return score
