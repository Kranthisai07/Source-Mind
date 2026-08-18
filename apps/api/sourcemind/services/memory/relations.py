"""
Stage 7b — RELATE: Detect semantic relationships between new and existing memories.

Two-pass algorithm:
  Pass 1 — vector similarity scan:
    For each new memory, find top-10 existing memories with cosine distance < 0.25.
  Pass 2 — LLM classification (only for distance < 0.20):
    Ask Claude to classify the relationship (updates/extends/derives/unrelated).
    Write MemoryRelation if confidence >= 0.85.

Conflict detection (separate from relations):
  Two memories with distance < 0.15 from different contributors,
  with no existing relation, that Claude classifies as 'updates' → write MemoryConflict.

Thresholds (see ADR-006):
  0.25 — scan radius (cosine distance)
  0.20 — LLM classification trigger
  0.15 — conflict detection
  0.85 — minimum confidence to persist a relation

Usage:
  detector = RelationDetector(anthropic_client)
  await detector.detect(session, new_memories, workspace_id)
"""

from __future__ import annotations

import json
import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.models.memory import Memory
from sourcemind.models.memory_conflict import (
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    MemoryConflict,
)
from sourcemind.models.memory_relation import MemoryRelation, RelationType

log = structlog.get_logger(__name__)

_SCAN_RADIUS = 0.25       # cosine distance: scan for candidates
_LLM_RADIUS = 0.20        # cosine distance: trigger LLM classification
_CONFLICT_RADIUS = 0.15   # cosine distance: trigger conflict check
_MIN_CONFIDENCE = 0.85    # minimum LLM confidence to persist


async def _classify_relation(
    client: object,
    existing_content: str,
    new_content: str,
) -> tuple[str, float, str]:
    """
    Ask Claude Haiku to classify the relationship.
    Returns (relation_type, confidence, reasoning).
    Uses Haiku for cost efficiency on this high-frequency classification.
    """
    prompt = (
        "Classify the relationship between these two statements.\n\n"
        f"Statement A: {existing_content}\n\n"
        f"Statement B: {new_content}\n\n"
        "Choose exactly one:\n"
        '- "updates": B contradicts or replaces A (A becomes outdated)\n'
        '- "extends": B adds new detail to A (both remain valid)\n'
        '- "derives": B is a logical inference from A\n'
        '- "unrelated": no meaningful relationship\n\n'
        'Respond in JSON only: {"relation": string, "confidence": float, "reasoning": string}'
    )
    try:
        response = await client.messages.create(  # type: ignore[union-attr]
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(response.content[0].text)
        return (
            str(data.get("relation", "unrelated")),
            float(data.get("confidence", 0.0)),
            str(data.get("reasoning", "")),
        )
    except Exception as exc:
        log.debug("relation_classify_error", error=str(exc))
        return "unrelated", 0.0, ""


async def _maybe_create_conflict(
    session: AsyncSession,
    client: object,
    new_memory: Memory,
    cand_id: uuid.UUID,
    cand_content: str,
    distance: float,
) -> None:
    """
    Check whether two similar memories from different contributors form a conflict.
    Only writes MemoryConflict if:
      - Different contributors
      - No existing relation between them
      - Claude classifies as 'updates' (contradiction)
    """
    # Get contributors
    r1 = await session.execute(
        text(
            "SELECT user_id::text FROM attributions WHERE memory_id = CAST(:id AS uuid) "
            "ORDER BY created_at ASC LIMIT 1"
        ),
        {"id": str(new_memory.id)},
    )
    r2 = await session.execute(
        text(
            "SELECT user_id::text FROM attributions WHERE memory_id = CAST(:id AS uuid) "
            "ORDER BY created_at ASC LIMIT 1"
        ),
        {"id": str(cand_id)},
    )
    row1 = r1.fetchone()
    row2 = r2.fetchone()
    if not row1 or not row2:
        return
    if row1[0] == row2[0]:
        return  # Same contributor — not a conflict

    # Check for existing relation
    existing = await session.execute(
        text(
            "SELECT id FROM memory_relations "
            "WHERE (source_memory_id = CAST(:a AS uuid) AND target_memory_id = CAST(:b AS uuid)) "
            "OR (source_memory_id = CAST(:b AS uuid) AND target_memory_id = CAST(:a AS uuid))"
        ),
        {"a": str(new_memory.id), "b": str(cand_id)},
    )
    if existing.fetchone():
        return  # Already related

    # LLM contradiction check
    relation_type, confidence, _ = await _classify_relation(client, cand_content, new_memory.content)
    if relation_type != "updates" or confidence < _MIN_CONFIDENCE:
        return

    try:
        conflict = MemoryConflict(
            workspace_id=new_memory.workspace_id,
            memory_a_id=cand_id,
            memory_b_id=new_memory.id,
            conflict_type=ConflictType.CONTRADICTION,
            # Placeholder only — overwritten by compute_conflict_severity()
            # below, once the row exists and can be scored.
            severity=ConflictSeverity.MEDIUM,
            status=ConflictStatus.OPEN,
            similarity_score=1.0 - distance,
            explanation=f"Two memories from different contributors appear to contradict. Confidence: {confidence:.2f}",
        )
        session.add(conflict)
        await session.flush()

        # Score it now that the row exists. Severity is derived, so the
        # placeholder above must never survive past creation.
        from sourcemind.services.conflict.severity import compute_conflict_severity

        await compute_conflict_severity(session, conflict.id)

        log.info(
            "conflict_detected",
            memory_a=str(cand_id),
            memory_b=str(new_memory.id),
            similarity=1.0 - distance,
            confidence=confidence,
        )
    except Exception as exc:
        log.debug("conflict_insert_skipped", error=str(exc))


class RelationDetector:
    """
    Detects semantic relationships and conflicts between memories.

    The Anthropic client is injected at construction — never instantiated internally.
    This makes testing easy: pass a mock client directly.
    """

    def __init__(self, client: object) -> None:
        self._client = client

    async def detect(
        self,
        session: AsyncSession,
        new_memories: list[Memory],
        workspace_id: uuid.UUID,
    ) -> None:
        """
        Stage 7b entry point.

        For each new memory with a valid embedding, finds candidate existing memories
        and runs LLM classification. Writes MemoryRelation and MemoryConflict rows.
        """
        new_ids = {str(m.id) for m in new_memories}

        for memory in new_memories:
            if memory.embedding is None:
                continue

            embedding_str = "[" + ",".join(str(f) for f in memory.embedding) + "]"

            result = await session.execute(
                text("""
                    SELECT
                        id::text,
                        content,
                        embedding <=> CAST(:emb AS vector) AS dist
                    FROM memories
                    WHERE workspace_id = CAST(:ws_id AS uuid)
                      AND current_version = TRUE
                      AND deleted_at IS NULL
                      AND embedding IS NOT NULL
                      AND id::text != :mem_id
                    ORDER BY embedding <=> CAST(:emb AS vector)
                    LIMIT 10
                """),
                {
                    "emb": embedding_str,
                    "ws_id": str(workspace_id),
                    "mem_id": str(memory.id),
                },
            )
            candidates = result.fetchall()

            for cand_id_str, cand_content, distance in candidates:
                # Skip other memories in this same batch
                if cand_id_str in new_ids:
                    continue
                if distance > _SCAN_RADIUS:
                    continue

                cand_id = uuid.UUID(cand_id_str)

                if distance <= _LLM_RADIUS:
                    relation_type, confidence, _ = await _classify_relation(
                        self._client, cand_content, memory.content
                    )

                    if relation_type != "unrelated" and confidence >= _MIN_CONFIDENCE:
                        # Savepoint isolates the duplicate-edge case so we don't
                        # roll back unrelated rows already flushed in this transaction.
                        try:
                            async with session.begin_nested():
                                relation = MemoryRelation(
                                    source_memory_id=memory.id,
                                    target_memory_id=cand_id,
                                    relation_type=relation_type,
                                    confidence=confidence,
                                    similarity_score=1.0 - distance,
                                    detected_by="pipeline",
                                )
                                session.add(relation)
                                await session.flush()

                                # If new memory supersedes existing: retire existing
                                if relation_type == RelationType.UPDATES:
                                    await session.execute(
                                        text(
                                            "UPDATE memories SET current_version = FALSE "
                                            "WHERE id = CAST(:id AS uuid)"
                                        ),
                                        {"id": cand_id_str},
                                    )

                            log.info(
                                "relation_detected",
                                from_id=str(memory.id),
                                to_id=cand_id_str,
                                type=relation_type,
                                confidence=confidence,
                                distance=distance,
                            )
                        except IntegrityError as exc:
                            # Duplicate edge (UniqueConstraint) — savepoint already rolled back.
                            log.debug("relation_insert_skipped", error=str(exc))

                if distance <= _CONFLICT_RADIUS:
                    await _maybe_create_conflict(session, self._client, memory, cand_id, cand_content, distance)

        await session.flush()


# ── Backward-compatible module-level function ─────────────────────────────────

async def detect_relations(
    session: AsyncSession,
    new_memories: list[Memory],
    workspace_id: uuid.UUID,
    anthropic_client: object | None = None,
) -> None:
    """Module-level wrapper. Celery task uses this; pass client explicitly for testability."""
    if anthropic_client is None:
        from anthropic import AsyncAnthropic
        from sourcemind.core.config import get_settings
        settings = get_settings()
        anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    await RelationDetector(anthropic_client).detect(session, new_memories, workspace_id)

    # Recompute importance for all newly-related memories
    from sourcemind.services.memory.importance import recompute_importance
    for memory in new_memories:
        await recompute_importance(session, memory.id)

        # importance_score feeds conflict severity, so rescore any conflict
        # touching this memory or severity silently goes stale behind it.
        from sourcemind.services.conflict.severity import recompute_severity_for_memory

        await recompute_severity_for_memory(session, memory.id)
