"""
Knowledge Handoff System.

When a contributor is removed from a workspace, their memories are classified
by criticality and ownership is transferred with full attribution preserved.

Tier classification (see ADR-009):
  Tier 1 — CRITICAL: importance_score > 0.8, no other significant contributor
  Tier 2 — IMPORTANT: importance_score > 0.5, not tier 1
  Tier 3 — STANDARD: everything else with any attribution

Successor suggestion: find top contributor to semantically similar memories.
Attribution records are NEVER modified — only new records are appended.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# Thresholds (see ADR-009)
_TIER_1_IMPORTANCE = 0.8
_TIER_2_IMPORTANCE = 0.5
_TIER_1_SOLO_THRESHOLD = 0.3   # No other contributor has > 30% attribution
_SEMANTIC_SIMILARITY = 0.3    # Cosine distance threshold for successor search
_HANDOFF_WINDOW_DAYS = 30


@dataclass
class MemoryHandoffItem:
    """One memory in a departing user's handoff with its tier and successor."""

    memory_id: str
    content: str
    importance_score: float
    tier: int
    suggested_successor_id: str | None = None
    suggested_successor_name: str | None = None
    successor_confidence: float = 0.0


@dataclass
class HandoffSummary:
    """Tier-bucketed summary of all memories owned by a departing user."""

    departing_user_id: str
    departing_user_name: str
    total_memories_with_attribution: int
    tier_1_critical: list[MemoryHandoffItem] = field(default_factory=list)
    tier_2_important: list[MemoryHandoffItem] = field(default_factory=list)
    tier_3_standard_count: int = 0
    handoff_record_id: str | None = None
    handoff_expires_at: str | None = None


async def classify_memories(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    departing_user_id: uuid.UUID,
) -> HandoffSummary:
    """
    Classify all memories where the departing user has attribution into tiers.
    """
    # Get departing user info
    user_result = await session.execute(
        text("SELECT display_name, email FROM users WHERE id = :uid::uuid"),
        {"uid": str(departing_user_id)},
    )
    user_row = user_result.fetchone()
    user_name = (user_row[0] or user_row[1]) if user_row else str(departing_user_id)

    # Find all memories this user has attribution for
    result = await session.execute(
        text("""
            SELECT DISTINCT
                m.id::text,
                m.content,
                m.importance_score,
                -- Check for other significant contributors
                (
                    SELECT COUNT(*) FROM attributions a2
                    WHERE a2.memory_id = m.id
                    AND a2.user_id != :uid::uuid
                    AND a2.contribution_weight > :solo_threshold
                    AND a2.created_at = (
                        SELECT MAX(a3.created_at) FROM attributions a3
                        WHERE a3.memory_id = m.id AND a3.user_id = a2.user_id
                    )
                ) AS other_significant_contributors,
                -- Check if memory is referenced by others
                (
                    SELECT COUNT(*) FROM memory_relations mr
                    WHERE mr.target_memory_id = m.id
                    AND mr.relation_type != 'updates'
                ) AS incoming_references
            FROM memories m
            JOIN attributions a ON a.memory_id = m.id
                AND a.user_id = :uid::uuid
            WHERE m.workspace_id = :ws_id::uuid
              AND m.current_version = TRUE
              AND m.deleted_at IS NULL
        """),
        {
            "uid": str(departing_user_id),
            "ws_id": str(workspace_id),
            "solo_threshold": _TIER_1_SOLO_THRESHOLD,
        },
    )
    rows = result.fetchall()

    summary = HandoffSummary(
        departing_user_id=str(departing_user_id),
        departing_user_name=user_name,
        total_memories_with_attribution=len(rows),
    )

    for row in rows:
        mem_id, content, importance, other_contributors, incoming_refs = row
        importance = importance or 0.5

        item = MemoryHandoffItem(
            memory_id=mem_id,
            content=content[:200] + "..." if len(content) > 200 else content,
            importance_score=importance,
            tier=3,
        )

        # Tier 1: critical
        is_critical = (
            importance > _TIER_1_IMPORTANCE
            and (other_contributors == 0 or incoming_refs > 0)
        )
        # Tier 2: important
        is_important = importance > _TIER_2_IMPORTANCE and not is_critical

        if is_critical:
            item.tier = 1
            # Find suggested successor
            successor = await _find_successor(session, mem_id, str(departing_user_id))
            if successor:
                item.suggested_successor_id = successor["user_id"]
                item.suggested_successor_name = successor["name"]
                item.successor_confidence = successor["confidence"]
            summary.tier_1_critical.append(item)
        elif is_important:
            item.tier = 2
            summary.tier_2_important.append(item)
        else:
            summary.tier_3_standard_count += 1

    return summary


async def _find_successor(
    session: AsyncSession,
    memory_id: str,
    departing_user_id: str,
) -> dict[str, Any] | None:
    """
    Find the most qualified successor for a memory.

    Strategy:
      1. Find memories semantically similar to this one (dist < 0.3)
      2. Find contributors to those memories (excluding departing user)
      3. Rank by contribution_weight DESC, then recency
    """
    result = await session.execute(
        text("""
            SELECT DISTINCT
                a.user_id::text,
                COALESCE(u.display_name, u.email) AS name,
                AVG(a.contribution_weight) AS avg_weight
            FROM memory_relations mr
            JOIN memories m2 ON (
                m2.id = mr.source_memory_id OR m2.id = mr.target_memory_id
            )
            JOIN attributions a ON a.memory_id = m2.id
                AND a.user_id::text != :dep_uid
            JOIN users u ON u.id = a.user_id
            WHERE (mr.source_memory_id = :mid::uuid OR mr.target_memory_id = :mid::uuid)
              AND m2.id::text != :mid
              AND m2.current_version = TRUE
              AND m2.deleted_at IS NULL
            GROUP BY a.user_id, u.display_name, u.email
            ORDER BY avg_weight DESC
            LIMIT 1
        """),
        {"mid": memory_id, "dep_uid": departing_user_id},
    )
    row = result.fetchone()
    if not row:
        return None
    user_id, name, avg_weight = row
    return {"user_id": user_id, "name": name, "confidence": float(avg_weight)}


async def assign_memory(
    session: AsyncSession,
    handoff_record_id: uuid.UUID,
    memory_id: uuid.UUID,
    new_owner_id: uuid.UUID,
    departing_user_id: uuid.UUID,
    note: str | None = None,
) -> dict[str, Any]:
    """
    Assign a memory to a new owner.

    The departing user's current attribution share is split:
      - New owner gets 40% of the departing user's weight going forward
      - Historical records are NEVER modified (append-only)

    Returns the updated attribution breakdown.
    """
    # Get current attribution for departing user (latest record)
    attr_result = await session.execute(
        text("""
            SELECT contribution_weight FROM attributions
            WHERE memory_id = :mid::uuid AND user_id = :uid::uuid
            ORDER BY created_at DESC LIMIT 1
        """),
        {"mid": str(memory_id), "uid": str(departing_user_id)},
    )
    attr_row = attr_result.fetchone()
    departing_weight = float(attr_row[0]) if attr_row else 0.0

    # 40% of the departing user's share goes to the new owner
    transfer_amount = departing_weight * 0.4

    # Insert new attribution record for new owner (APPEND-ONLY)
    from sourcemind.models.attribution import Attribution, AttributionActionType
    new_attr = Attribution(
        memory_id=memory_id,
        user_id=new_owner_id,
        contribution_weight=transfer_amount,
        trigger_action="transfer",
        char_diff_score=None,
        semantic_score=None,
        temporal_score=None,
        structural_score=None,
        approval_score=None,
    )
    session.add(new_attr)

    # Update handoff_assignments record
    await session.execute(
        text("""
            UPDATE handoff_assignments
            SET new_owner_id = :owner::uuid, assigned_at = NOW(), note = :note
            WHERE handoff_id = :hid::uuid AND memory_id = :mid::uuid
        """),
        {
            "owner": str(new_owner_id),
            "note": note,
            "hid": str(handoff_record_id),
            "mid": str(memory_id),
        },
    )

    # Increment assigned_count in handoff_records
    await session.execute(
        text(
            "UPDATE handoff_records SET assigned_count = assigned_count + 1 "
            "WHERE id = :hid::uuid"
        ),
        {"hid": str(handoff_record_id)},
    )

    await session.flush()

    log.info(
        "handoff_assigned",
        memory_id=str(memory_id),
        new_owner_id=str(new_owner_id),
        transfer_amount=transfer_amount,
    )

    # Recompute importance now that ownership has transferred
    from sourcemind.services.memory.importance import recompute_importance
    await recompute_importance(session, memory_id)

    # Return updated attribution breakdown
    updated_result = await session.execute(
        text("""
            SELECT a.user_id::text, COALESCE(u.display_name, u.email), a.contribution_weight
            FROM attributions a
            JOIN users u ON u.id = a.user_id
            WHERE a.memory_id = :mid::uuid
              AND a.created_at = (
                SELECT MAX(a2.created_at) FROM attributions a2
                WHERE a2.memory_id = :mid::uuid AND a2.user_id = a.user_id
              )
            ORDER BY a.contribution_weight DESC
        """),
        {"mid": str(memory_id)},
    )
    breakdown = [
        {"user_id": r[0], "name": r[1], "contribution_weight": float(r[2])}
        for r in updated_result.fetchall()
    ]
    return {"memory_id": str(memory_id), "attribution": breakdown}


async def create_handoff_record(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    departing_user_id: uuid.UUID,
    initiated_by: uuid.UUID,
    summary: HandoffSummary,
) -> uuid.UUID:
    """Create a handoff_records row and seed handoff_assignments for tier 1+2."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=_HANDOFF_WINDOW_DAYS)

    # Insert handoff_records
    result = await session.execute(
        text("""
            INSERT INTO handoff_records
                (workspace_id, departing_user_id, initiated_by,
                 tier_1_count, tier_2_count, tier_3_count, expires_at)
            VALUES
                (:ws::uuid, :dep::uuid, :init::uuid,
                 :t1, :t2, :t3, :exp)
            RETURNING id::text
        """),
        {
            "ws": str(workspace_id),
            "dep": str(departing_user_id),
            "init": str(initiated_by),
            "t1": len(summary.tier_1_critical),
            "t2": len(summary.tier_2_important),
            "t3": summary.tier_3_standard_count,
            "exp": expires_at.isoformat(),
        },
    )
    handoff_id_str = result.scalar()
    handoff_id = uuid.UUID(handoff_id_str)

    # Seed handoff_assignments for tier 1 and tier 2
    for item in summary.tier_1_critical + summary.tier_2_important:
        await session.execute(
            text("""
                INSERT INTO handoff_assignments (handoff_id, memory_id, tier)
                VALUES (:hid::uuid, :mid::uuid, :tier)
                ON CONFLICT DO NOTHING
            """),
            {"hid": str(handoff_id), "mid": item.memory_id, "tier": item.tier},
        )

    # Update workspace_members status to 'departing'
    await session.execute(
        text(
            "UPDATE workspace_members SET status = 'departing' "
            "WHERE workspace_id = :ws::uuid AND user_id = :uid::uuid"
        ),
        {"ws": str(workspace_id), "uid": str(departing_user_id)},
    )

    await session.flush()
    return handoff_id


async def complete_handoff(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    departing_user_id: uuid.UUID,
    handoff_record_id: uuid.UUID,
) -> dict[str, Any]:
    """
    Complete the handoff: mark member as departed, flag unassigned tier-1 memories.
    """
    # Count unassigned tier-1 memories
    unassigned_result = await session.execute(
        text("""
            SELECT COUNT(*) FROM handoff_assignments
            WHERE handoff_id = :hid::uuid AND tier = 1 AND new_owner_id IS NULL
        """),
        {"hid": str(handoff_record_id)},
    )
    unassigned_count = unassigned_result.scalar() or 0

    # Update workspace_members to departed
    await session.execute(
        text(
            "UPDATE workspace_members "
            "SET status = 'departed', departed_at = NOW() "
            "WHERE workspace_id = :ws::uuid AND user_id = :uid::uuid"
        ),
        {"ws": str(workspace_id), "uid": str(departing_user_id)},
    )

    # Mark handoff_records as completed
    await session.execute(
        text(
            "UPDATE handoff_records SET status = 'completed', completed_at = NOW() "
            "WHERE id = :hid::uuid"
        ),
        {"hid": str(handoff_record_id)},
    )

    await session.flush()

    log.info(
        "handoff_completed",
        departing_user_id=str(departing_user_id),
        workspace_id=str(workspace_id),
        unassigned_count=unassigned_count,
    )

    return {
        "status": "completed",
        "departing_user_id": str(departing_user_id),
        "unassigned_tier_1_count": unassigned_count,
    }
