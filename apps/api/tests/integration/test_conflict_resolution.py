"""
Integration tests for the conflict resolution workflow.

Tests the full lifecycle: open → under_review → resolved.
Mocks only the database session.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_conflict_lifecycle():
    """
    Verify the complete open → under_review → resolved transition.
    Steps:
      1. mark_under_review() transitions from open to under_review
      2. resolve_conflict(kept_a) marks the conflict as resolved
    """
    from sourcemind.services.conflict.resolver import mark_under_review, resolve_conflict

    conflict_id = uuid.uuid4()
    memory_a_id = uuid.uuid4()
    memory_b_id = uuid.uuid4()
    reviewer_id = uuid.uuid4()

    # ── Step 1: mark_under_review ──────────────────────────────────
    mock_session = AsyncMock()
    update_result = MagicMock()
    update_result.rowcount = 1
    mock_session.execute = AsyncMock(return_value=update_result)

    ok = await mark_under_review(mock_session, conflict_id, reviewer_id)
    assert ok is True

    # Verify an UPDATE was executed with the right status filter
    call_args = mock_session.execute.call_args_list[0]
    query_str = str(call_args[0][0])
    assert "under_review" in query_str
    assert "open" in query_str

    # ── Step 2: resolve_conflict(kept_a) ──────────────────────────
    mock_session2 = AsyncMock()

    # First execute returns the conflict row for lookup (memory_a_id, memory_b_id, workspace_id)
    conflict_row = MagicMock()
    conflict_row.fetchone = MagicMock(return_value=(
        str(memory_a_id), str(memory_b_id), str(uuid.uuid4()),
    ))

    resolve_result = MagicMock()
    resolve_result.rowcount = 1

    call_count = [0]

    async def execute_side(query, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return conflict_row
        # recompute_importance queries return None to bail out early
        r = MagicMock()
        r.fetchone = MagicMock(return_value=None)
        r.rowcount = 1
        return r

    mock_session2.execute = AsyncMock(side_effect=execute_side)

    resolved = await resolve_conflict(
        session=mock_session2,
        conflict_id=conflict_id,
        resolver_id=reviewer_id,
        resolution_type="kept_a",
        resolution_note="Memory A is correct.",
    )
    assert resolved is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_merged_resolution_creates_new_memory():
    """
    resolve_conflict(merged) should:
      1. Deprecate both memory_a and memory_b (set current_version=FALSE)
      2. INSERT a new merged Memory row
      3. Update conflict status to 'resolved'
    """
    from sourcemind.services.conflict.resolver import resolve_conflict

    conflict_id = uuid.uuid4()
    memory_a_id = uuid.uuid4()
    memory_b_id = uuid.uuid4()
    resolver_id = uuid.uuid4()

    mock_session = AsyncMock()
    add_calls = []
    mock_session.add = MagicMock(side_effect=lambda x: add_calls.append(x))
    mock_session.flush = AsyncMock()

    # Track SQL calls
    execute_calls = []

    async def execute_side(query, params=None):
        execute_calls.append(str(query))
        mock_result = MagicMock()
        mock_result.rowcount = 1
        query_str = str(query)
        if "inbound_count" in query_str or "importance_score" in query_str:
            # recompute_importance query — return None to bail out early
            mock_result.fetchone = MagicMock(return_value=None)
        else:
            mock_result.fetchone = MagicMock(return_value=(
                str(memory_a_id), str(memory_b_id), str(uuid.uuid4()),
            ))
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        return mock_result

    mock_session.execute = AsyncMock(side_effect=execute_side)

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=conflict_id,
        resolver_id=resolver_id,
        resolution_type="merged",
        resolution_note="Combined both.",
        merged_content="Merged: Memory A and Memory B content combined.",
    )

    assert result is True

    # A new Memory object should have been added to the session
    from sourcemind.models.memory import Memory
    added_memories = [c for c in add_calls if isinstance(c, Memory)]
    assert len(added_memories) == 1
    assert added_memories[0].content == "Merged: Memory A and Memory B content combined."
