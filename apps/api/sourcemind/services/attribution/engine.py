"""
Attribution engine — manages attribution records for memories.

Functions:
  create_initial_attribution()  — Stage 6 of ingestion pipeline
  recompute_attribution()       — Called on every PATCH /v1/memories/:id

The full 5-signal algorithm (ADR-007) runs in recompute_attribution().
Records are APPEND-ONLY (enforced by DB trigger from ADR-002).
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.models.attribution import Attribution, AttributionActionType, AttributionEdit
from sourcemind.services.attribution.scorer import EditEvent, get_scorer

log = structlog.get_logger(__name__)


async def create_initial_attribution(
    session: AsyncSession,
    memory_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    source_type: str,
    idempotency_key: str | None = None,
) -> Attribution:
    """
    Create the initial attribution and edit records for a new memory.

    Creates:
      1. AttributionEdit — records the creation event (content_before=None)
      2. Attribution — snapshot assigning 100% weight to the submitter

    Both records are flushed (not committed) so they participate in the
    same transaction as the Memory insert.
    """
    # 1. Create the edit record first (Attribution.edit_id references it)
    edit = AttributionEdit(
        memory_id=memory_id,
        editor_id=user_id,
        content_before=None,
        content_after=content,
        edit_position=1,
        action_type=AttributionActionType.CREATE,
        idempotency_key=idempotency_key,
    )
    session.add(edit)
    await session.flush()  # needed to get edit.id

    # 2. Create the attribution snapshot referencing the edit
    attribution = Attribution(
        memory_id=memory_id,
        user_id=user_id,
        contribution_weight=1.0,
        char_diff_score=1.0,
        semantic_score=1.0,
        temporal_score=1.0,
        structural_score=1.0,
        approval_score=0.0,  # No explicit approval yet
        trigger_action=AttributionActionType.CREATE,
        edit_id=edit.id,
    )
    session.add(attribution)
    await session.flush()

    log.debug(
        "initial_attribution_created",
        memory_id=str(memory_id),
        user_id=str(user_id),
        source_type=source_type,
    )
    return attribution


async def recompute_attribution(
    session: AsyncSession,
    memory_id: uuid.UUID,
    editor_id: uuid.UUID,
    content_before: str,
    content_after: str,
    action_type: str = "edit",
    idempotency_key: str | None = None,
) -> list[Attribution]:
    """
    Recompute attribution for a memory after an edit.

    Steps:
      1. Load all historical AttributionEdit records for this memory chain
      2. Append the new edit event
      3. Run 5-signal scorer over the full edit history
      4. INSERT new Attribution records (one per contributor)
         — NEVER UPDATE existing records (append-only)
      5. INSERT the new AttributionEdit record

    Returns the new Attribution records.
    """
    # Get next edit position
    pos_result = await session.execute(
        text(
            "SELECT COALESCE(MAX(edit_position), 0) + 1 FROM attribution_edits "
            "WHERE memory_id = :mid::uuid"
        ),
        {"mid": str(memory_id)},
    )
    edit_position = pos_result.scalar() or 1

    # Load full edit history for scorer
    hist_result = await session.execute(
        text("""
            SELECT ae.editor_id::text, ae.content_before, ae.content_after,
                   ae.edit_position, ae.action_type
            FROM attribution_edits ae
            WHERE ae.memory_id = :mid::uuid
            ORDER BY ae.edit_position ASC
        """),
        {"mid": str(memory_id)},
    )
    rows = hist_result.fetchall()

    # Build edit event list including the new edit
    edits = [
        EditEvent(
            user_id=row[0],
            content_before=row[1],
            content_after=row[2],
            edit_position=row[3],
            action_type=row[4],
        )
        for row in rows
    ]
    edits.append(
        EditEvent(
            user_id=str(editor_id),
            content_before=content_before,
            content_after=content_after,
            edit_position=edit_position,
            action_type=action_type,
        )
    )

    # Run 5-signal scorer
    scorer = get_scorer()
    normalized = scorer.compute_scores(edits)

    # Insert new AttributionEdit record
    new_edit = AttributionEdit(
        memory_id=memory_id,
        editor_id=editor_id,
        content_before=content_before,
        content_after=content_after,
        edit_position=edit_position,
        action_type=action_type,
        idempotency_key=idempotency_key,
    )
    session.add(new_edit)
    await session.flush()

    # Insert new Attribution records (one per contributor in normalized result)
    new_attributions: list[Attribution] = []
    for norm in normalized:
        attribution = Attribution(
            memory_id=memory_id,
            user_id=uuid.UUID(norm.user_id),
            contribution_weight=norm.contribution_weight,
            char_diff_score=norm.char_diff_score,
            semantic_score=norm.semantic_score,
            temporal_score=norm.temporal_score,
            structural_score=norm.structural_score,
            approval_score=norm.approval_score,
            trigger_action=action_type,
            edit_id=new_edit.id,
        )
        session.add(attribution)
        new_attributions.append(attribution)

    await session.flush()

    log.info(
        "attribution_recomputed",
        memory_id=str(memory_id),
        editor_id=str(editor_id),
        contributors=len(normalized),
        edit_position=edit_position,
    )
    return new_attributions
