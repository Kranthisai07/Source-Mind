"""
Integration tests for the knowledge handoff workflow.

Tests the full lifecycle: initiate → assign → complete.
Mocks only the database session.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.services.attribution.handoff import (
    HandoffSummary,
    MemoryHandoffItem,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_handoff_lifecycle():
    """
    Full handoff lifecycle:
      1. classify_memories() — returns tier breakdown for departing user
      2. create_handoff_record() — creates DB record + seeds assignments
      3. assign_memory() — transfers 40% attribution to new owner
      4. complete_handoff() — marks member as departed
    """
    from sourcemind.services.attribution.handoff import (
        assign_memory,
        classify_memories,
        complete_handoff,
        create_handoff_record,
    )

    workspace_id = uuid.uuid4()
    departing_user_id = uuid.uuid4()
    new_owner_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    handoff_id = uuid.uuid4()

    # ── Step 1: classify_memories ─────────────────────────────────
    mock_session = AsyncMock()

    async def classify_execute(query, params=None):
        mock_result = MagicMock()
        query_str = str(query)
        # Successor search: only query with AVG( aggregate
        if "AVG(" in query_str:
            mock_result.fetchone = MagicMock(return_value=None)
        # Main classify query: has importance_score column
        elif "importance_score" in query_str:
            mock_result.fetchall = MagicMock(return_value=[
                (str(memory_id), "Critical memory content here.", 0.85, 0, 1),
            ])
        # Simple user lookup: display_name + email from users
        elif "display_name" in query_str and "users" in query_str:
            mock_result.fetchone = MagicMock(return_value=("Alice", "alice@example.com"))
        else:
            mock_result.fetchone = MagicMock(return_value=None)
            mock_result.fetchall = MagicMock(return_value=[])
        return mock_result

    mock_session.execute = AsyncMock(side_effect=classify_execute)

    summary = await classify_memories(mock_session, workspace_id, departing_user_id)

    assert summary.total_memories_with_attribution == 1
    assert len(summary.tier_1_critical) == 1
    assert summary.tier_1_critical[0].tier == 1

    # ── Step 2: create_handoff_record ────────────────────────────
    mock_session2 = AsyncMock()
    mock_session2.flush = AsyncMock()

    call_count = [0]

    async def create_execute(query, params=None):
        call_count[0] += 1
        mock_result = MagicMock()
        query_str = str(query)
        if "INSERT INTO handoff_records" in query_str:
            mock_result.scalar = MagicMock(return_value=str(handoff_id))
        else:
            mock_result.scalar = MagicMock(return_value=None)
            mock_result.rowcount = 1
        return mock_result

    mock_session2.execute = AsyncMock(side_effect=create_execute)

    created_handoff_id = await create_handoff_record(
        session=mock_session2,
        workspace_id=workspace_id,
        departing_user_id=departing_user_id,
        initiated_by=admin_id,
        summary=summary,
    )

    assert created_handoff_id == handoff_id

    # ── Step 3: assign_memory ────────────────────────────────────
    mock_session3 = AsyncMock()
    mock_session3.flush = AsyncMock()
    add_calls = []
    mock_session3.add = MagicMock(side_effect=lambda x: add_calls.append(x))

    assign_call_count = [0]

    async def assign_execute(query, params=None):
        assign_call_count[0] += 1
        mock_result = MagicMock()
        query_str = str(query)
        if "contribution_weight" in query_str and "ORDER BY created_at DESC" in query_str:
            # Current attribution weight for departing user
            mock_result.fetchone = MagicMock(return_value=(0.8,))
        elif "SELECT" in query_str and "ORDER BY" in query_str:
            # Updated breakdown
            mock_result.fetchall = MagicMock(return_value=[
                (str(departing_user_id), "Alice", 0.8),
                (str(new_owner_id), "Bob", 0.32),
            ])
        else:
            mock_result.rowcount = 1
            mock_result.fetchone = MagicMock(return_value=None)
        return mock_result

    mock_session3.execute = AsyncMock(side_effect=assign_execute)

    assign_result = await assign_memory(
        session=mock_session3,
        handoff_record_id=handoff_id,
        memory_id=memory_id,
        new_owner_id=new_owner_id,
        departing_user_id=departing_user_id,
        note="Assigned to Bob as domain expert.",
    )

    assert "attribution" in assign_result
    assert len(assign_result["attribution"]) == 2

    # New attribution record added (append-only)
    from sourcemind.models.attribution import Attribution
    added_attrs = [c for c in add_calls if isinstance(c, Attribution)]
    assert len(added_attrs) == 1
    assert abs(added_attrs[0].contribution_weight - 0.32) < 0.01  # 40% of 0.8

    # ── Step 4: complete_handoff ─────────────────────────────────
    mock_session4 = AsyncMock()
    mock_session4.flush = AsyncMock()

    complete_call_count = [0]

    async def complete_execute(query, params=None):
        complete_call_count[0] += 1
        mock_result = MagicMock()
        query_str = str(query)
        if "COUNT" in query_str and "tier = 1" in query_str:
            # No unassigned tier-1 memories
            mock_result.scalar = MagicMock(return_value=0)
        else:
            mock_result.rowcount = 1
            mock_result.scalar = MagicMock(return_value=None)
        return mock_result

    mock_session4.execute = AsyncMock(side_effect=complete_execute)

    complete_result = await complete_handoff(
        session=mock_session4,
        workspace_id=workspace_id,
        departing_user_id=departing_user_id,
        handoff_record_id=handoff_id,
    )

    assert complete_result["status"] == "completed"
    assert complete_result["unassigned_tier_1_count"] == 0
    assert complete_result["departing_user_id"] == str(departing_user_id)
