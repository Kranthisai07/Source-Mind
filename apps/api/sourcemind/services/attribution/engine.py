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
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class _HistoryRow:
    edit_id: uuid.UUID
    editor_id: str
    content_before: str | None
    content_after: str
    edit_position: int
    action_type: str


async def _load_chain_history(
    session: AsyncSession, memory_id: uuid.UUID
) -> list[_HistoryRow]:
    """
    Load every attribution edit recorded for ``memory_id`` and its ancestors.

    Ancestry is the ``parent_memory_id`` chain only, constrained to the
    memory's workspace. Memories that merely share a document are NOT part
    of the chain. Each edit row appears once. Ordering is by chain version
    first (so legacy chains whose per-version positions restarted at 1 still
    order correctly), then recorded position, creation time and id.

    Reads attribution_edits only - never attributions - so carried-forward
    snapshot rows can never be mistaken for contribution events.
    """
    result = await session.execute(
        text("""
            WITH RECURSIVE chain AS (
                SELECT id, parent_memory_id, version, workspace_id, 0 AS depth
                FROM memories WHERE id = CAST(:mid AS uuid)
                UNION ALL
                SELECT m.id, m.parent_memory_id, m.version, m.workspace_id, c.depth + 1
                FROM memories m
                JOIN chain c ON m.id = c.parent_memory_id
                WHERE m.workspace_id = c.workspace_id AND c.depth < 10000
            )
            SELECT ae.id, ae.editor_id::text, ae.content_before, ae.content_after,
                   ae.edit_position, ae.action_type
            FROM attribution_edits ae
            JOIN chain c ON c.id = ae.memory_id
            ORDER BY c.version ASC, ae.edit_position ASC, ae.created_at ASC, ae.id ASC
        """),
        {"mid": str(memory_id)},
    )
    seen: set[uuid.UUID] = set()
    rows: list[_HistoryRow] = []
    for r in result.fetchall():
        if r[0] in seen:
            continue
        seen.add(r[0])
        rows.append(_HistoryRow(r[0], r[1], r[2], r[3], r[4], r[5]))
    return rows


_SNAPSHOT_VALUE_FIELDS = (
    "contribution_weight",
    "char_diff_score",
    "semantic_score",
    "temporal_score",
    "structural_score",
    "approval_score",
)


def _snapshot_identity(row: Any) -> tuple:
    """Everything that distinguishes one stored snapshot row from another.

    Values are compared exactly as stored; there is deliberately no float
    tolerance, because two distinct stored shares are not the same share.
    trigger_action and edit_id are provenance and are part of identity.
    """
    return (
        *(getattr(row, f) for f in _SNAPSHOT_VALUE_FIELDS),
        row.trigger_action,
        row.edit_id,
    )


def _select_latest_snapshots(rows: list[Any], memory_id: uuid.UUID) -> dict[uuid.UUID, Any]:
    """
    Pick the current snapshot row per user, or refuse.

    The newest ``created_at`` wins. Every candidate at that timestamp is
    inspected before anything is chosen:
      * candidates identical in values AND provenance (only the row id
        differs) collapse to one;
      * otherwise precedence is established only when every candidate is a
        recorded edit of the SAME memory with a distinct ``edit_position``;
      * anything else - in particular an edit-linked row against a
        snapshot-only row such as a transfer or merge, whose relative
        chronology is unknown - raises AttributionStateConflictError.
    A row id is never used to break a tie.
    """
    from sourcemind.core.exceptions import AttributionStateConflictError

    by_user: dict[uuid.UUID, list[Any]] = {}
    for row in rows:
        by_user.setdefault(row.user_id, []).append(row)

    chosen: dict[uuid.UUID, Any] = {}
    for user_id, candidates in by_user.items():
        newest = max(c.created_at for c in candidates)
        tied = [c for c in candidates if c.created_at == newest]
        distinct: dict[tuple, Any] = {}
        for c in tied:
            distinct.setdefault(_snapshot_identity(c), c)
        if len(distinct) == 1:
            chosen[user_id] = next(iter(distinct.values()))
            continue
        options = list(distinct.values())
        positions = [o.edit_position for o in options]
        edit_memories = {o.edit_memory_id for o in options}
        ordered = (
            all(o.edit_id is not None for o in options)
            and len(edit_memories) == 1
            and None not in edit_memories
            and len(set(positions)) == len(positions)
        )
        if not ordered:
            log.warning(
                "attribution_snapshot_order_ambiguous",
                memory_id=str(memory_id),
                user_id=str(user_id),
                candidates=len(options),
            )
            raise AttributionStateConflictError(
                "Stored attribution has equally ordered snapshots that disagree and "
                "no recorded order between them; this update was not applied "
                "(ambiguous_snapshot_order)."
            )
        chosen[user_id] = max(options, key=lambda o: o.edit_position)
    return chosen


async def plan_carry_forward(session: AsyncSession, memory_id: uuid.UUID) -> list[Any]:
    """Read-only: the snapshot rows a new version of ``memory_id`` would inherit.

    Raises AttributionStateConflictError when the current snapshot is
    ambiguous. Performs no writes, so callers can run it before creating a
    version and leave nothing behind on refusal.
    """
    result = await session.execute(
        text("""
            SELECT a.user_id, a.contribution_weight, a.char_diff_score, a.semantic_score,
                   a.temporal_score, a.structural_score, a.approval_score,
                   a.trigger_action, a.edit_id, a.created_at,
                   e.edit_position AS edit_position, e.memory_id AS edit_memory_id,
                   e.action_type AS edit_action_type
            FROM attributions a
            LEFT JOIN attribution_edits e ON e.id = a.edit_id
            WHERE a.memory_id = CAST(:mid AS uuid)
        """),
        {"mid": str(memory_id)},
    )
    return list(_select_latest_snapshots(result.fetchall(), memory_id).values())


def _is_represented_by_history(
    row: Any, history_edit_ids: set[uuid.UUID], history_editors: set[str]
) -> bool:
    """Can the recorded edit history reproduce this selected snapshot row?

    All three must hold, judged on the row actually selected for the user:
      1. it is linked (edit_id) to an edit in the loaded ancestry;
      2. its trigger_action equals that edit's action_type. Rows the engine
         writes always satisfy this; a handoff transfer or a merge never does,
         even if it were stamped with an edit_id;
      3. its user is an editor somewhere in that history. This is necessary
         but no longer sufficient: a user who genuinely edited earlier and
         later received a transfer is still protected, because the selected
         transfer row fails (1) and (2).
    """
    return (
        row.edit_id is not None
        and row.edit_id in history_edit_ids
        and row.edit_action_type is not None
        and str(row.trigger_action) == str(row.edit_action_type)
        and str(row.user_id) in history_editors
    )


async def assert_edit_preserves_inherited_contributors(
    session: AsyncSession, memory_id: uuid.UUID
) -> None:
    """
    Refuse a substantive edit that would drop known inherited contributors.

    The scorer only sees recorded edit events. A selected snapshot row that
    those events cannot reproduce (a merged memory's source shares, a handoff
    transfer, or a metadata-only descendant that carried such rows) would be
    silently discarded by recomputing. No events are invented and no
    weighting is applied; the edit is refused until a policy exists (D-022).
    The rule is provenance-based per selected row, not a comparison of user
    ids, so identity overlap with genuine editors does not weaken it.

    An unresolved external author has no snapshot rows and is not affected.
    Read-only.
    """
    from sourcemind.core.exceptions import AttributionStateConflictError

    selected = await plan_carry_forward(session, memory_id)
    history = await _load_chain_history(session, memory_id)
    history_edit_ids = {h.edit_id for h in history}
    history_editors = {h.editor_id for h in history}
    unrepresented = [
        r for r in selected if not _is_represented_by_history(r, history_edit_ids, history_editors)
    ]
    if unrepresented:
        log.warning(
            "edit_would_discard_inherited_contributors",
            memory_id=str(memory_id),
            contributors=len(unrepresented),
            trigger_actions=sorted({str(r.trigger_action) for r in unrepresented}),
        )
        raise AttributionStateConflictError(
            "This memory has attribution recorded only as snapshots (for example from a "
            "merge or a handoff transfer) that its edit history cannot reproduce; editing its "
            "content would discard them, so the edit was not applied "
            "(inherited_contributors_not_representable)."
        )


async def carry_forward_attribution(
    session: AsyncSession,
    from_memory_id: uuid.UUID,
    to_memory_id: uuid.UUID,
) -> list[Attribution]:
    """
    Expose a parent version's current attribution on a new version whose
    content did not change (tags-only update).

    Copies the snapshot row chosen by ``plan_carry_forward`` per user,
    preserving weights, signal scores, trigger_action and edit_id exactly.
    No attribution_edits row is created, so this is not a contribution event
    and repeating it adds nothing to the scorer's history. Raises
    AttributionStateConflictError (before any insert) when the parent's
    snapshot is ambiguous.
    """
    carried: list[Attribution] = []
    for row in await plan_carry_forward(session, from_memory_id):
        attribution = Attribution(
            memory_id=to_memory_id,
            user_id=row.user_id,
            contribution_weight=row.contribution_weight,
            char_diff_score=row.char_diff_score,
            semantic_score=row.semantic_score,
            temporal_score=row.temporal_score,
            structural_score=row.structural_score,
            approval_score=row.approval_score,
            trigger_action=row.trigger_action,
            edit_id=row.edit_id,
        )
        session.add(attribution)
        carried.append(attribution)
    await session.flush()
    return carried


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
      1. Load all historical AttributionEdit records across the version ancestry
      2. Append the new edit event
      3. Run 5-signal scorer over the full edit history
      4. INSERT new Attribution records (one per contributor)
         — NEVER UPDATE existing records (append-only)
      5. INSERT the new AttributionEdit record

    Returns the new Attribution records.
    """
    # Load the full contribution history across this memory's version
    # ancestry. Each PATCH creates a new memory row, so history recorded
    # against earlier versions lives under their ids, not memory_id.
    history = await _load_chain_history(session, memory_id)
    edit_position = max([len(history), *(row.edit_position for row in history)]) + 1

    # Build edit event list including the new edit
    edits = [
        EditEvent(
            user_id=row.editor_id,
            content_before=row.content_before,
            content_after=row.content_after,
            edit_position=row.edit_position,
            action_type=row.action_type,
        )
        for row in history
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
