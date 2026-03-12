"""
Conflict resolution workflow.

Phase 2 detects conflicts and writes them to memory_conflicts.
Phase 3 adds the full resolution lifecycle:

  open → under_review → resolved | deferred

Resolution types:
  kept_a    Memory A is correct; Memory B is retired (current_version=False)
  kept_b    Memory B is correct; Memory A is retired
  merged    Both deprecated; new merged memory created
  split     Both kept, tagged differently
  deferred  Postponed with revisit_at date

AI suggestion is generated asynchronously when GET /v1/conflicts/:id is called
and stored in memory_conflicts.suggested_resolution JSONB.
See ADR-008 for rationale on advisory-only AI suggestions.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

_SUGGESTION_PROMPT = """\
Two statements in a team knowledge base are in conflict.
Analyze them and suggest the best resolution.

Statement A (contributed by {contributor_a}):
"{content_a}"

Statement B (contributed by {contributor_b}):
"{content_b}"

Consider:
- Which is more specific and evidence-based?
- Could both be true in different contexts?
- Is one an update of the other?
- Can they be merged into one accurate statement?

Respond in JSON:
{{
  "suggested_resolution": "kept_a|kept_b|merged|split",
  "reasoning": "2-3 sentence explanation",
  "merged_content": "string if merged, null otherwise",
  "confidence": 0.0-1.0
}}"""


@dataclass
class ConflictDetail:
    """Full conflict detail including both memory contents and AI suggestion."""
    conflict_id: str
    status: str
    conflict_type: str
    severity: str
    similarity_score: float
    explanation: str | None
    memory_a_id: str
    memory_a_content: str
    memory_b_id: str
    memory_b_content: str
    suggested_resolution: dict[str, Any] | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    revisit_at: datetime | None
    created_at: datetime | None


async def get_conflict_detail(
    session: AsyncSession,
    conflict_id: uuid.UUID,
    anthropic_client: object | None = None,
) -> ConflictDetail | None:
    """
    Fetch full conflict detail. If no AI suggestion yet, generate one asynchronously.
    """
    result = await session.execute(
        text("""
            SELECT
                mc.id::text, mc.status, mc.conflict_type, mc.severity,
                mc.similarity_score, mc.explanation,
                mc.memory_a_id::text, ma.content,
                mc.memory_b_id::text, mb.content,
                mc.suggested_resolution,
                mc.reviewed_by::text, mc.reviewed_at, mc.revisit_at, mc.created_at,
                COALESCE(ua.display_name, ua.email) AS contributor_a,
                COALESCE(ub.display_name, ub.email) AS contributor_b
            FROM memory_conflicts mc
            JOIN memories ma ON ma.id = mc.memory_a_id
            JOIN memories mb ON mb.id = mc.memory_b_id
            LEFT JOIN attributions aa ON aa.memory_id = mc.memory_a_id
                AND aa.created_at = (SELECT MAX(a2.created_at) FROM attributions a2 WHERE a2.memory_id = mc.memory_a_id)
            LEFT JOIN users ua ON ua.id = aa.user_id
            LEFT JOIN attributions ab ON ab.memory_id = mc.memory_b_id
                AND ab.created_at = (SELECT MAX(a3.created_at) FROM attributions a3 WHERE a3.memory_id = mc.memory_b_id)
            LEFT JOIN users ub ON ub.id = ab.user_id
            WHERE mc.id = :cid::uuid
        """),
        {"cid": str(conflict_id)},
    )
    row = result.fetchone()
    if not row:
        return None

    (cid, status, ctype, severity, sim, explanation,
     a_id, a_content, b_id, b_content,
     suggested, reviewed_by, reviewed_at, revisit_at, created_at,
     contrib_a, contrib_b) = row

    # Generate AI suggestion if missing and client provided
    if suggested is None and anthropic_client is not None:
        suggested = await _generate_suggestion(
            session, conflict_id,
            a_content, b_content,
            contrib_a or "Unknown", contrib_b or "Unknown",
            anthropic_client,
        )

    return ConflictDetail(
        conflict_id=cid,
        status=status,
        conflict_type=ctype,
        severity=severity,
        similarity_score=sim,
        explanation=explanation,
        memory_a_id=a_id,
        memory_a_content=a_content,
        memory_b_id=b_id,
        memory_b_content=b_content,
        suggested_resolution=suggested,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        revisit_at=revisit_at,
        created_at=created_at,
    )


async def _generate_suggestion(
    session: AsyncSession,
    conflict_id: uuid.UUID,
    content_a: str,
    content_b: str,
    contributor_a: str,
    contributor_b: str,
    client: object,
) -> dict[str, Any] | None:
    """Call Claude to generate a conflict resolution suggestion and store it."""
    prompt = _SUGGESTION_PROMPT.format(
        contributor_a=contributor_a,
        content_a=content_a,
        contributor_b=contributor_b,
        content_b=content_b,
    )
    try:
        response = await client.messages.create(  # type: ignore[union-attr]
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestion = json.loads(response.content[0].text)

        # Store in DB
        await session.execute(
            text(
                "UPDATE memory_conflicts SET suggested_resolution = :s::jsonb "
                "WHERE id = :cid::uuid"
            ),
            {"s": json.dumps(suggestion), "cid": str(conflict_id)},
        )
        log.info("conflict_suggestion_generated", conflict_id=str(conflict_id))
        return suggestion
    except Exception as exc:
        log.warning("conflict_suggestion_failed", error=str(exc), conflict_id=str(conflict_id))
        return None


async def mark_under_review(
    session: AsyncSession,
    conflict_id: uuid.UUID,
    reviewer_id: uuid.UUID,
) -> bool:
    """Transition conflict: open → under_review. Returns True if successful."""
    result = await session.execute(
        text(
            "UPDATE memory_conflicts "
            "SET status = 'under_review', reviewed_by = :uid::uuid, reviewed_at = NOW() "
            "WHERE id = :cid::uuid AND status = 'open' "
            "RETURNING id"
        ),
        {"cid": str(conflict_id), "uid": str(reviewer_id)},
    )
    return result.fetchone() is not None


async def resolve_conflict(
    session: AsyncSession,
    conflict_id: uuid.UUID,
    resolver_id: uuid.UUID,
    resolution_type: str,
    resolution_note: str | None = None,
    merged_content: str | None = None,
    revisit_at: datetime | None = None,
    tag_a: str | None = None,
    tag_b: str | None = None,
) -> bool:
    """
    Apply a resolution decision to a conflict.

    resolution_type: kept_a | kept_b | merged | split | deferred
    """
    # Fetch conflict and memory IDs
    result = await session.execute(
        text("""
            SELECT memory_a_id::text, memory_b_id::text, workspace_id::text
            FROM memory_conflicts WHERE id = :cid::uuid
        """),
        {"cid": str(conflict_id)},
    )
    row = result.fetchone()
    if not row:
        return False

    mem_a_id, mem_b_id, ws_id = row

    if resolution_type == "kept_a":
        await session.execute(
            text("UPDATE memories SET current_version = FALSE WHERE id = :id::uuid"),
            {"id": mem_b_id},
        )
        new_status = "resolved"

    elif resolution_type == "kept_b":
        await session.execute(
            text("UPDATE memories SET current_version = FALSE WHERE id = :id::uuid"),
            {"id": mem_a_id},
        )
        new_status = "resolved"

    elif resolution_type == "merged":
        if not merged_content:
            raise ValueError("merged_content required for resolution_type='merged'")
        # Deprecate both
        await session.execute(
            text("UPDATE memories SET current_version = FALSE WHERE id = ANY(ARRAY[:a::uuid, :b::uuid])"),
            {"a": mem_a_id, "b": mem_b_id},
        )
        # Create merged memory — 50/50 attribution handled by separate process
        from sourcemind.models.memory import Memory
        import hashlib
        merged = Memory(
            workspace_id=uuid.UUID(ws_id),
            content=merged_content,
            content_hash=hashlib.sha256(merged_content.encode()).hexdigest(),
            version=1,
            current_version=True,
        )
        session.add(merged)
        await session.flush()
        new_status = "resolved"

    elif resolution_type == "split":
        if not tag_a or not tag_b:
            raise ValueError("tag_a and tag_b required for resolution_type='split'")
        await session.execute(
            text("""
                UPDATE memories SET tags = array_append(COALESCE(tags, ARRAY[]::text[]), :tag)
                WHERE id = :id::uuid
            """),
            {"tag": tag_a, "id": mem_a_id},
        )
        await session.execute(
            text("""
                UPDATE memories SET tags = array_append(COALESCE(tags, ARRAY[]::text[]), :tag)
                WHERE id = :id::uuid
            """),
            {"tag": tag_b, "id": mem_b_id},
        )
        new_status = "resolved"

    elif resolution_type == "deferred":
        if not revisit_at:
            raise ValueError("revisit_at required for resolution_type='deferred'")
        await session.execute(
            text(
                "UPDATE memory_conflicts "
                "SET status = 'deferred', revisit_at = :rat "
                "WHERE id = :cid::uuid "
                "RETURNING id"
            ),
            {"rat": revisit_at.isoformat(), "cid": str(conflict_id)},
        )
        log.info("conflict_deferred", conflict_id=str(conflict_id), revisit_at=str(revisit_at))
        return True

    else:
        raise ValueError(f"Unknown resolution_type: {resolution_type!r}")

    # Update conflict record
    await session.execute(
        text("""
            UPDATE memory_conflicts
            SET status = :status,
                resolver_id = :rid::uuid,
                resolved_at = NOW(),
                resolution_note = :note
            WHERE id = :cid::uuid
        """),
        {
            "status": new_status,
            "rid": str(resolver_id),
            "note": resolution_note,
            "cid": str(conflict_id),
        },
    )

    log.info(
        "conflict_resolved",
        conflict_id=str(conflict_id),
        resolution_type=resolution_type,
        resolver_id=str(resolver_id),
    )
    return True
