"""
Workspace analytics service.

Endpoints it serves:
  GET /v1/workspaces/:id/analytics/overview
  GET /v1/workspaces/:id/analytics/contribution-map
  GET /v1/workspaces/:id/analytics/knowledge-gaps

Knowledge Health Score formula:
  coverage_score    = 1 - (single_contributor_memories / total_memories)
  freshness_score   = updated_in_90_days / total_important_memories
  conflict_score    = 1 - clamp(open_conflicts / total_memories, 0, 1)
  attribution_score = multi_contributor_memories / total_memories
  health = 0.30*coverage + 0.30*freshness + 0.25*conflict + 0.15*attribution
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _compute_health_score(
    total_memories: int,
    single_contributor_count: int,
    updated_in_90_days: int,
    total_important: int,
    open_conflicts: int,
    multi_contributor_count: int,
) -> float:
    """Compute the composite health score [0.0–1.0]."""
    if total_memories == 0:
        return 1.0

    coverage = 1.0 - (single_contributor_count / total_memories)
    freshness = updated_in_90_days / max(total_important, 1)
    conflict = 1.0 - _clamp(open_conflicts / total_memories, 0.0, 1.0)
    attribution = multi_contributor_count / total_memories

    score = (
        coverage * 0.30
        + freshness * 0.30
        + conflict * 0.25
        + attribution * 0.15
    )
    return round(_clamp(score, 0.0, 1.0), 4)


async def get_overview(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    redis_ttl: int = 300,
) -> dict[str, Any]:
    """
    Return workspace analytics overview.
    Results are cached in Redis for 300 seconds (5 minutes).
    """
    from sourcemind.core.redis_client import get_redis
    cache_key = f"analytics:overview:{workspace_id}"
    redis = get_redis()

    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    ws = str(workspace_id)

    # Total memories + contributors
    totals = await session.execute(
        text("""
            SELECT
                COUNT(DISTINCT m.id) AS total_memories,
                COUNT(DISTINCT a.user_id) AS total_contributors
            FROM memories m
            LEFT JOIN attributions a ON a.memory_id = m.id
            WHERE m.workspace_id = :ws::uuid
              AND m.current_version = TRUE
              AND m.deleted_at IS NULL
        """),
        {"ws": ws},
    )
    row = totals.fetchone()
    total_memories = row[0] if row else 0
    total_contributors = row[1] if row else 0

    # Memories created in last 30 days
    recent = await session.execute(
        text("""
            SELECT COUNT(*) FROM memories
            WHERE workspace_id = :ws::uuid
              AND current_version = TRUE
              AND deleted_at IS NULL
              AND created_at > NOW() - INTERVAL '30 days'
        """),
        {"ws": ws},
    )
    memories_last_30 = recent.scalar() or 0

    # Open conflicts
    conflicts = await session.execute(
        text(
            "SELECT COUNT(*) FROM memory_conflicts "
            "WHERE workspace_id = :ws::uuid AND status = 'open'"
        ),
        {"ws": ws},
    )
    open_conflicts = conflicts.scalar() or 0

    # Single-contributor memories
    single_contrib = await session.execute(
        text("""
            SELECT COUNT(*) FROM (
                SELECT m.id FROM memories m
                JOIN attributions a ON a.memory_id = m.id
                WHERE m.workspace_id = :ws::uuid
                  AND m.current_version = TRUE
                  AND m.deleted_at IS NULL
                GROUP BY m.id
                HAVING COUNT(DISTINCT a.user_id) = 1
            ) sub
        """),
        {"ws": ws},
    )
    single_contributor_count = single_contrib.scalar() or 0

    # Multi-contributor memories
    multi_contrib = await session.execute(
        text("""
            SELECT COUNT(*) FROM (
                SELECT m.id FROM memories m
                JOIN attributions a ON a.memory_id = m.id
                WHERE m.workspace_id = :ws::uuid
                  AND m.current_version = TRUE
                  AND m.deleted_at IS NULL
                GROUP BY m.id
                HAVING COUNT(DISTINCT a.user_id) > 1
            ) sub
        """),
        {"ws": ws},
    )
    multi_contributor_count = multi_contrib.scalar() or 0

    # Updated in 90 days (important memories)
    freshness = await session.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE updated_at > NOW() - INTERVAL '90 days'
                    OR created_at > NOW() - INTERVAL '90 days') AS updated,
                COUNT(*) AS total_important
            FROM memories
            WHERE workspace_id = :ws::uuid
              AND current_version = TRUE
              AND deleted_at IS NULL
              AND importance_score > 0.5
        """),
        {"ws": ws},
    )
    fresh_row = freshness.fetchone()
    updated_in_90 = fresh_row[0] if fresh_row else 0
    total_important = fresh_row[1] if fresh_row else 0

    health_score = _compute_health_score(
        total_memories=total_memories,
        single_contributor_count=single_contributor_count,
        updated_in_90_days=updated_in_90,
        total_important=total_important,
        open_conflicts=open_conflicts,
        multi_contributor_count=multi_contributor_count,
    )

    # Top contributors
    top_result = await session.execute(
        text("""
            SELECT
                a.user_id::text,
                COALESCE(u.display_name, u.email) AS name,
                COUNT(DISTINCT a.memory_id) AS memory_count,
                AVG(a.contribution_weight) AS avg_weight
            FROM attributions a
            JOIN users u ON u.id = a.user_id
            JOIN memories m ON m.id = a.memory_id
            WHERE m.workspace_id = :ws::uuid
              AND m.current_version = TRUE
              AND m.deleted_at IS NULL
            GROUP BY a.user_id, u.display_name, u.email
            ORDER BY memory_count DESC
            LIMIT 5
        """),
        {"ws": ws},
    )
    top_contributors = [
        {
            "user_id": r[0],
            "name": r[1],
            "memory_count": r[2],
            "avg_contribution_pct": round(float(r[3]), 4),
            "primary_topics": [],  # Populated by tag analysis if available
        }
        for r in top_result.fetchall()
    ]

    # Recent activity (last 20 events)
    activity_result = await session.execute(
        text("""
            SELECT
                ae.memory_id::text,
                ae.action_type,
                COALESCE(u.display_name, u.email) AS name,
                ae.created_at,
                LEFT(m.content, 60) AS content_preview
            FROM attribution_edits ae
            JOIN users u ON u.id = ae.editor_id
            JOIN memories m ON m.id = ae.memory_id
            WHERE m.workspace_id = :ws::uuid
            ORDER BY ae.created_at DESC
            LIMIT 20
        """),
        {"ws": ws},
    )

    # Keys match AttributionActionType enum values stored in attribution_edits.
    _action_labels = {
        "create": "created",
        "edit": "edited",
        "approve": "approved",
        "reject": "rejected",
        "merge": "merged",
    }
    recent_activity = [
        {
            "memory_id": r[0],
            "action": r[1],
            "contributor_name": r[2],
            "timestamp": str(r[3]) if r[3] else None,
            "description": (
                f"{r[2]} {_action_labels.get(r[1], r[1])} "
                f"'{(r[4] or '').strip()[:50]}'"
            ),
        }
        for r in activity_result.fetchall()
    ]

    # Compute individual breakdown scores for the UI health gauge
    coverage_score = round(1.0 - (single_contributor_count / max(total_memories, 1)), 4)
    freshness_score = round(updated_in_90 / max(total_important, 1), 4)
    conflict_score = round(1.0 - _clamp(open_conflicts / max(total_memories, 1), 0.0, 1.0), 4)
    attribution_score = round(multi_contributor_count / max(total_memories, 1), 4)

    result_data: dict[str, Any] = {
        "total_memories": total_memories,
        "total_contributors": total_contributors,
        "avg_memories_per_contributor": (
            round(total_memories / total_contributors, 2) if total_contributors > 0 else 0
        ),
        "memories_created_last_30_days": memories_last_30,
        "open_conflicts": open_conflicts,
        "knowledge_health_score": health_score,
        "health_breakdown": {
            "coverage": coverage_score,
            "freshness": freshness_score,
            "conflict_ratio": conflict_score,
            "attribution": attribution_score,
        },
        "top_contributors": top_contributors,
        "recent_activity": recent_activity,
    }

    # Cache result
    await redis.setex(cache_key, redis_ttl, json.dumps(result_data, default=str))
    return result_data


async def get_contribution_map(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """Return per-contributor breakdown of knowledge ownership.

    Resolved in a single query: the `mem_contributor_counts` CTE pre-computes
    how many contributors each memory has, which lets us roll up collaboration
    rate per user without an N+1 loop.
    """
    ws = str(workspace_id)

    result = await session.execute(
        text("""
            WITH workspace_memories AS (
                SELECT id FROM memories
                WHERE workspace_id = :ws::uuid
                  AND current_version = TRUE
                  AND deleted_at IS NULL
            ),
            mem_contributor_counts AS (
                SELECT a.memory_id, COUNT(DISTINCT a.user_id) AS contributor_count
                FROM attributions a
                JOIN workspace_memories wm ON wm.id = a.memory_id
                GROUP BY a.memory_id
            )
            SELECT
                a.user_id::text                                                         AS uid,
                COALESCE(u.display_name, u.email)                                       AS name,
                u.email                                                                 AS email,
                COUNT(DISTINCT m.id) FILTER (WHERE ae.edit_position = 1)                AS created,
                COUNT(DISTINCT m.id)                                                    AS influenced,
                AVG(a.contribution_weight)                                              AS avg_weight,
                MAX(ae.created_at)                                                      AS last_contribution,
                COUNT(DISTINCT m.id) FILTER (WHERE mcc.contributor_count > 1)           AS collab_count
            FROM attributions a
            JOIN users u ON u.id = a.user_id
            JOIN workspace_memories m ON m.id = a.memory_id
            LEFT JOIN attribution_edits ae ON ae.memory_id = a.memory_id
                AND ae.editor_id = a.user_id
            LEFT JOIN mem_contributor_counts mcc ON mcc.memory_id = m.id
            GROUP BY a.user_id, u.display_name, u.email
            ORDER BY influenced DESC
        """),
        {"ws": ws},
    )
    rows = result.fetchall()

    contributors = []
    for row in rows:
        uid, name, email, created, influenced, avg_weight, last_contrib, collab_count = row
        login = email.split("@")[0] if email else name.lower().replace(" ", ".")
        collab_rate = round((collab_count or 0) / max(influenced or 1, 1), 4)

        contributors.append({
            "user_id": uid,
            "name": name,
            "login": login,
            "total_memories_created": created or 0,
            "total_memories_influenced": influenced or 0,
            "avg_contribution_pct": round(float(avg_weight or 0), 4),
            "knowledge_domains": [],
            "collaboration_rate": collab_rate,
            "last_contribution_at": str(last_contrib) if last_contrib else None,
        })

    return {"contributors": contributors}


async def get_knowledge_gaps(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> dict[str, Any]:
    """
    Identify areas where knowledge is thin or at risk.

    Gap types:
      single_contributor  — one person has > 90% attribution, never edited by others
      no_recent_update    — important memory (>0.7), version=1, older than 90 days
      high_conflict_area  — tag cluster where >20% of memories have open conflicts
    """
    ws = str(workspace_id)
    gaps = []

    # single_contributor
    single_result = await session.execute(
        text("""
            SELECT COUNT(*) FROM (
                SELECT m.id FROM memories m
                JOIN attributions a ON a.memory_id = m.id
                WHERE m.workspace_id = :ws::uuid
                  AND m.current_version = TRUE
                  AND m.deleted_at IS NULL
                GROUP BY m.id
                HAVING COUNT(DISTINCT a.user_id) = 1
                   AND MAX(a.contribution_weight) > 0.9
            ) s
        """),
        {"ws": ws},
    )
    single_count = single_result.scalar() or 0
    if single_count > 0:
        gaps.append({
            "gap_type": "single_contributor",
            "description": f"{single_count} memories are owned exclusively by one person (>90% attribution) with no other contributors.",
            "affected_memories": single_count,
            "risk_level": "high" if single_count > 10 else "medium",
            "recommendation": "Encourage knowledge sharing. Consider pair-editing critical memories to distribute ownership.",
        })

    # no_recent_update (important memories never updated, > 90 days old)
    stale_result = await session.execute(
        text("""
            SELECT COUNT(*) FROM memories
            WHERE workspace_id = :ws::uuid
              AND current_version = TRUE
              AND deleted_at IS NULL
              AND importance_score > 0.7
              AND version = 1
              AND created_at < NOW() - INTERVAL '90 days'
        """),
        {"ws": ws},
    )
    stale_count = stale_result.scalar() or 0
    if stale_count > 0:
        gaps.append({
            "gap_type": "no_recent_update",
            "description": f"{stale_count} important memories (importance > 0.7) have never been updated and are older than 90 days.",
            "affected_memories": stale_count,
            "risk_level": "medium",
            "recommendation": "Review and refresh stale high-importance memories to ensure accuracy.",
        })

    # high_conflict_area (tag-based, simplified)
    conflict_result = await session.execute(
        text("""
            SELECT
                unnest(m.tags) AS tag,
                COUNT(DISTINCT m.id) AS total,
                COUNT(DISTINCT mc.id) AS conflicts
            FROM memories m
            LEFT JOIN memory_conflicts mc ON
                (mc.memory_a_id = m.id OR mc.memory_b_id = m.id)
                AND mc.status = 'open'
            WHERE m.workspace_id = :ws::uuid
              AND m.current_version = TRUE
              AND m.deleted_at IS NULL
              AND m.tags IS NOT NULL
            GROUP BY tag
            HAVING COUNT(DISTINCT mc.id)::float / NULLIF(COUNT(DISTINCT m.id), 0) > 0.2
        """),
        {"ws": ws},
    )
    conflict_rows = conflict_result.fetchall()
    for row in conflict_rows:
        tag, total, conflicts = row
        gaps.append({
            "gap_type": "high_conflict_area",
            "description": f"Tag '{tag}' has {conflicts}/{total} memories with open conflicts (>{20}% conflict rate).",
            "affected_memories": total,
            "risk_level": "high",
            "recommendation": f"Resolve open conflicts in the '{tag}' knowledge area to improve consistency.",
        })

    return {"gaps": gaps}


async def who_would_know(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
    limit: int = 5,
) -> dict[str, Any]:
    """
    Given a free-text query, return the contributors most likely to know about it.

    Algorithm:
      1. BM25 text search (content_tsv) to find up to 30 matching memories,
         scored by ts_rank_cd.
      2. Join with attributions, weight each contributor's score by
         (attribution_score × memory_rank).
      3. Return top-N contributors sorted by weighted expertise score,
         including their top matching memory snippets.
    """
    ws = str(workspace_id)

    result = await session.execute(
        text("""
            WITH matching AS (
                SELECT
                    m.id,
                    LEFT(m.content, 120)                                   AS preview,
                    m.category,
                    ts_rank_cd(m.content_tsv, plainto_tsquery('english', :q)) AS rank
                FROM memories m
                WHERE m.workspace_id = :ws::uuid
                  AND m.current_version = TRUE
                  AND m.deleted_at IS NULL
                  AND m.content_tsv @@ plainto_tsquery('english', :q)
                ORDER BY rank DESC
                LIMIT 30
            ),
            scored AS (
                SELECT
                    a.user_id                                                  AS uid,
                    SUM(a.contribution_weight * mm.rank)                       AS weighted_score,
                    COUNT(DISTINCT a.memory_id)                                AS memory_count,
                    -- best-ranked memory for this contributor
                    (array_agg(mm.id       ORDER BY mm.rank DESC))[1]          AS top_memory_id,
                    (array_agg(mm.preview  ORDER BY mm.rank DESC))[1]          AS top_preview,
                    (array_agg(mm.category ORDER BY mm.rank DESC))[1]          AS top_category,
                    MAX(a.contribution_weight)                                 AS peak_attribution
                FROM attributions a
                JOIN matching mm ON mm.id = a.memory_id
                WHERE a.contribution_weight > 0.05
                GROUP BY a.user_id
            )
            SELECT
                s.uid::text,
                COALESCE(u.display_name, u.email)    AS name,
                u.email,
                s.weighted_score,
                s.memory_count,
                s.top_memory_id::text,
                s.top_preview,
                s.top_category,
                s.peak_attribution
            FROM scored s
            JOIN users u ON u.id = s.uid
            ORDER BY s.weighted_score DESC
            LIMIT :lim
        """),
        {"ws": ws, "q": query, "lim": limit},
    )
    rows = result.fetchall()

    if not rows:
        return {"query": query, "experts": []}

    # Normalise weighted_score to [0, 1] confidence relative to top scorer
    top_score = float(rows[0][3]) if rows else 1.0

    experts = []
    for row in rows:
        uid, name, email, w_score, mem_count, top_mid, top_prev, top_cat, peak_attr = row
        login = email.split("@")[0] if email else (name or "").lower().replace(" ", ".")
        confidence = round(min(float(w_score) / max(top_score, 1e-9), 1.0), 3)
        experts.append({
            "user_id": uid,
            "name": name,
            "login": login,
            "confidence": confidence,
            "memory_count": int(mem_count),
            "peak_attribution": round(float(peak_attr), 3),
            "top_memory": {
                "id": top_mid,
                "preview": (top_prev or "").strip(),
                "category": top_cat or "general",
            },
        })

    return {"query": query, "experts": experts}
