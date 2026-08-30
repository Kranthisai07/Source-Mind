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
      2. Attribution — snapshot scored by the 5-signal algorithm. The sole
         contributor normalises to 100% weight, but the per-signal values are
         computed rather than assumed.

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

    # 2. Score the creation with the SAME algorithm every later edit uses.
    #
    # This previously wrote the tuple (1.0, 1.0, 1.0, 1.0, 0.0) by hand - a
    # hardcoded duplicate of what the scorer computes for a first creation,
    # not a placeholder standing in for it. Removing the duplicate changes
    # exactly one value in practice, structural_score, and makes the rest
    # derived rather than asserted so they stay correct if the algorithm
    # changes.
    #
    # Four of the five signals are constant here by construction, and that is
    # the correct answer rather than a degenerate one:
    #
    #   S1 char diff  1.0  - _signal1_char_diff returns early on empty
    #                        `before`; there is nothing to diff against, so the
    #                        creator changed 100% of the content.
    #   S2 semantic   1.0  - compute_scores compares each contribution against
    #                        the LATEST version. With one edit those are the
    #                        same string, so the cosine is 1.0 by identity.
    #   S3 temporal   1.0  - 0.8^(1-1). This genuinely is position 1.
    #   S4 structural 1.0 or 0.0 - the only signal that varies. Every entity is
    #                        new when there is no `before`, so it is 1.0 when
    #                        the NER backend finds any entity and 0.0 when the
    #                        content has none. The old constant 1.0 was wrong
    #                        for entity-free content.
    #   S5 approval   0.0  - creating is not approving.
    #
    # contribution_weight normalises to 1.0 whatever the raw signals say,
    # because there is exactly one contributor. The algorithm discriminates
    # between contributors; with one of them there is nothing to discriminate.
    from sourcemind.services.attribution.scorer import EditEvent, get_scorer

    scored = get_scorer().compute_scores(
        [
            EditEvent(
                user_id=str(user_id),
                content_before=None,
                content_after=content,
                edit_position=1,
                action_type=AttributionActionType.CREATE.value,
            )
        ]
    )
    if not scored:
        # compute_scores returns [] only for an empty edit list, which cannot
        # happen here. Fail loudly rather than silently writing nothing.
        raise RuntimeError(
            f"scorer returned no attribution for memory {memory_id}"
        )
    signals = scored[0]

    attribution = Attribution(
        memory_id=memory_id,
        user_id=user_id,
        contribution_weight=signals.contribution_weight,
        char_diff_score=signals.char_diff_score,
        semantic_score=signals.semantic_score,
        temporal_score=signals.temporal_score,
        structural_score=signals.structural_score,
        approval_score=signals.approval_score,
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
            "WHERE memory_id = CAST(:mid AS uuid)"
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
            WHERE ae.memory_id = CAST(:mid AS uuid)
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
