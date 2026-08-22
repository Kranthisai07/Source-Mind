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
import re
import uuid
from typing import Any, NamedTuple

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

_CLASSIFY_MODEL = "claude-haiku-4-5-20251001"

# 1024 to match fact_extractor. Observed replies used 47-166 tokens, but
# conflict_summary is unbounded prose and a truncated reply is invalid
# JSON, which lands in exactly the silent-failure path this function has
# just had to be rescued from. Headroom is cheaper than that failure.
_CLASSIFY_MAX_TOKENS = 1024

# Format only. Everything about WHAT to decide stays in the user prompt,
# which the neutrality tests scan; this governs only the shape of the
# reply. Without it the model reliably wrapped its JSON in a markdown
# fence, json.loads failed on the leading backticks, and the handler
# below turned that into "unrelated" - so no relation and no conflict
# was ever written, in any workspace, for as long as this code existed.
_CLASSIFY_SYSTEM_PROMPT = (
    "You classify how two statements from a team knowledge base relate."
    "\n\nOutput format, strictly: reply with one raw JSON object and "
    "nothing else. No markdown, no code fences, no backticks, and no "
    "commentary before or after it. The first character of your reply "
    "must be { and the last must be }."
)


class Classification(NamedTuple):
    """One LLM verdict about a pair of memories.

    relation / confidence describe the knowledge-graph edge.
    is_conflict / conflict_summary are the conflict signal, and carry no
    opinion about which claim is correct.

    confidence measures certainty in the CLASSIFICATION, not in either
    statement being true. The two are different and only the first is
    something the model may judge.
    """

    relation: str
    confidence: float
    is_conflict: bool = False
    conflict_summary: str = ""


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply.

    Covers the shapes these replies actually take: a bare object, one
    wrapped in a ```json fence, and one with prose on either side.
    Stripping a literal "```json" prefix and "```" suffix
    would handle only the middle case and would still fail on an
    unterminated fence, so the fence is unwrapped when present and the
    outermost braces are used either way.

    Raises ValueError when there is no object at all, so the caller can
    report a real failure instead of silently calling it "no
    relationship".
    """
    text_ = raw.strip()

    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text_, re.DOTALL)
    if fenced:
        text_ = fenced.group(1).strip()

    start, end = text_.find("{"), text_.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in model reply: {raw[:200]!r}")

    parsed = json.loads(text_[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


async def _classify_relation(
    client: object,
    existing_content: str,
    new_content: str,
) -> Classification:
    """
    Ask Claude Haiku to classify the relationship.
    Returns (relation_type, confidence, reasoning).
    Uses Haiku for cost efficiency on this high-frequency classification.
    """
    prompt = (
        "Two statements from a team knowledge base are shown below. "
        "Describe their relationship. Do NOT judge which statement is "
        "correct, better supported, or more likely to be true. That "
        "decision belongs to a human reviewer.\n\n"
        f"Statement A: {existing_content}\n\n"
        f"Statement B: {new_content}\n\n"
        "First, pick the relationship type for the knowledge graph:\n"
        '- "updates": B states a different value for the same thing A describes\n'
        '- "extends": B adds detail to A\n'
        '- "derives": B follows logically from A\n'
        '- "unrelated": no meaningful relationship\n\n'
        "Second, decide whether the two are in conflict. They conflict when "
        "they are mutually exclusive claims about the same underlying "
        "decision point, meaning both cannot be true at once. Summarise the "
        "disagreement factually, stating what each side claims, without "
        "indicating which one is right.\n\n"
        "confidence is how certain you are that this relationship "
        "classification is correct. It is NOT a judgement about which "
        "statement is true.\n\n"
        "Respond in JSON only: "
        '{"relation": string, "confidence": float, "is_conflict": bool, '
        '"conflict_summary": string}'
    )
    # The call and the parse are distinct failures and are reported
    # distinctly. Both return the same neutral default, but a parse
    # failure means the model DID answer and its answer was thrown away -
    # a defect, not an absence of relationship. At debug level that
    # distinction stayed invisible while every relation and conflict in
    # the system silently failed to be written.
    try:
        response = await client.messages.create(  # type: ignore[union-attr]
            model=_CLASSIFY_MODEL,
            max_tokens=_CLASSIFY_MAX_TOKENS,
            system=_CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        log.error("relation_classify_api_error", error=str(exc))
        return Classification(relation="unrelated", confidence=0.0)

    raw_text = response.content[0].text
    try:
        data = _extract_json_object(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning(
            "relation_classify_unparseable",
            error=str(exc),
            raw_preview=raw_text[:200],
        )
        return Classification(relation="unrelated", confidence=0.0)

    return Classification(
        relation=str(data.get("relation", "unrelated")),
        confidence=float(data.get("confidence", 0.0)),
        is_conflict=bool(data.get("is_conflict", False)),
        conflict_summary=str(data.get("conflict_summary", "")),
    )


async def _maybe_create_conflict(
    session: AsyncSession,
    client: object,
    new_memory: Memory,
    cand_id: uuid.UUID,
    cand_content: str,
    distance: float,
    verdict: Classification | None = None,
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

    # detect() has usually classified this pair already; reuse that rather
    # than paying for a second identical call.
    if verdict is None:
        verdict = await _classify_relation(client, cand_content, new_memory.content)

    # A conflict is raised on the neutral mutual-exclusivity signal, not on
    # a supersession verdict. Keying off relation == 'updates' meant every
    # conflict originated from a judgement that one side was outdated,
    # which is the model picking a winner.
    if not verdict.is_conflict or verdict.confidence < _MIN_CONFIDENCE:
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
            explanation=(
                verdict.conflict_summary
                or "Two memories from different contributors make mutually "
                   "exclusive claims about the same point."
            ),
        )
        session.add(conflict)
        await session.flush()

        # Score the whole cluster, not just this row. Adding a conflict raises
        # the competing-claim count for the disputed memory, which changes the
        # severity of every OTHER unresolved conflict already on it — a second
        # rival can tip an existing conflict from medium to critical. Scoring
        # only the new row would leave those stale.
        #
        # recompute_severity_for_memory covers the new conflict too, since it
        # rescores every unresolved conflict touching cand_id.
        from sourcemind.services.conflict.severity import recompute_severity_for_memory

        await recompute_severity_for_memory(session, cand_id)

        log.info(
            "conflict_detected",
            memory_a=str(cand_id),
            memory_b=str(new_memory.id),
            similarity=1.0 - distance,
            confidence=verdict.confidence,
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
                    verdict = await _classify_relation(
                        self._client, cand_content, memory.content
                    )
                    relation_type = verdict.relation
                    confidence = verdict.confidence

                    # Conflict check runs BEFORE the relation write.
                    #
                    # _maybe_create_conflict skips any pair that is already
                    # related, which is meant to catch pairs related in an
                    # EARLIER run. Writing the relation first made that guard
                    # fire on the edge written moments earlier in this same
                    # loop, so a conflict could only ever be created when the
                    # relation insert happened to fail. Since _CONFLICT_RADIUS
                    # (0.15) is inside _LLM_RADIUS (0.20), every conflict
                    # candidate took that path — conflict detection was
                    # unreachable on the real ingestion path.
                    #
                    # The classification is passed through so the pair is not
                    # sent to the model a second time.
                    if distance <= _CONFLICT_RADIUS:
                        await _maybe_create_conflict(
                            session,
                            self._client,
                            memory,
                            cand_id,
                            cand_content,
                            distance,
                            verdict=verdict,
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


        await session.flush()

        # Post-processing lives HERE, not in a wrapper.
        #
        # Relations and conflicts have just been written, which changes both
        # inbound-relation counts (an importance_score input) and the number
        # of competing claims (a severity input). Both must be refreshed
        # before this returns.
        #
        # This used to sit in the module-level detect_relations() wrapper,
        # whose docstring claimed the Celery task used it. It did not —
        # workers/ingestion.py calls detect() directly — so on the real
        # ingestion path importance stayed at the 0.5 column default and
        # every conflict scored 'medium' from that placeholder forever.
        # Keeping it inside detect() means no caller can bypass it.
        from sourcemind.services.conflict.severity import recompute_severity_for_memory
        from sourcemind.services.memory.importance import recompute_importance

        for memory in new_memories:
            # Order matters: severity reads importance_score, so refresh it
            # first or severity is computed from the stale value.
            await recompute_importance(session, memory.id)
            await recompute_severity_for_memory(session, memory.id)
