"""Unit tests for the knowledge handoff service."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_session_with_rows(*row_sequences):
    """Create a mock session where each execute() call returns the next row sequence."""
    mock_session = AsyncMock()
    call_index = [0]
    sequences = list(row_sequences)

    async def execute_side_effect(stmt, params=None, **kwargs):
        idx = call_index[0]
        call_index[0] += 1
        r = MagicMock()

        if idx < len(sequences):
            rows = sequences[idx]
            if isinstance(rows, list):
                r.fetchall = MagicMock(return_value=rows)
                r.fetchone = MagicMock(return_value=rows[0] if rows else None)
                r.scalar = MagicMock(return_value=rows[0][0] if rows else 0)
            elif rows is None:
                r.fetchall = MagicMock(return_value=[])
                r.fetchone = MagicMock(return_value=None)
                r.scalar = MagicMock(return_value=0)
            else:
                r.fetchone = MagicMock(return_value=rows)
                r.scalar = MagicMock(return_value=rows[0] if rows else 0)
        else:
            r.fetchall = MagicMock(return_value=[])
            r.fetchone = MagicMock(return_value=None)
            r.scalar = MagicMock(return_value=0)

        return r

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    return mock_session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critical_tier_classification_single_contributor():
    """Memory with importance_score > 0.8 and no other contributors → Tier 1."""
    from sourcemind.services.attribution.handoff import classify_memories

    ws_id = uuid.uuid4()
    dep_user_id = uuid.uuid4()
    mem_id = str(uuid.uuid4())

    # Sequence: user info, memories with tier data
    mock_session = _make_session_with_rows(
        ("Dev User", "dev@example.com"),  # user info
        [(mem_id, "Critical memory content.", 0.9, 0, 0)],  # memories
    )

    summary = await classify_memories(mock_session, ws_id, dep_user_id)

    assert summary.total_memories_with_attribution == 1
    assert len(summary.tier_1_critical) == 1
    assert summary.tier_1_critical[0].importance_score == 0.9
    assert summary.tier_1_critical[0].tier == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critical_tier_classification_high_importance():
    """Memory with high importance AND incoming references → Tier 1."""
    from sourcemind.services.attribution.handoff import classify_memories

    ws_id = uuid.uuid4()
    dep_user_id = uuid.uuid4()
    mem_id = str(uuid.uuid4())

    mock_session = _make_session_with_rows(
        ("Expert User", "expert@example.com"),
        # importance=0.85, 0 other contributors, 2 incoming references
        [(mem_id, "Authoritative content.", 0.85, 0, 2)],
    )

    summary = await classify_memories(mock_session, ws_id, dep_user_id)

    assert len(summary.tier_1_critical) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successor_suggestion_finds_semantic_neighbor():
    """assign_memory should create new attribution record for new owner."""
    from sourcemind.services.attribution.handoff import assign_memory

    handoff_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    new_owner_id = uuid.uuid4()
    dep_user_id = uuid.uuid4()

    added_objects = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        r = MagicMock()
        if "SELECT contribution_weight" in stmt_str:
            r.fetchone = MagicMock(return_value=(0.8,))
        elif "SELECT a.user_id" in stmt_str:
            r.fetchall = MagicMock(return_value=[
                (str(new_owner_id), "Alice", 0.32)
            ])
        else:
            r.fetchone = MagicMock(return_value=None)
            r.fetchall = MagicMock(return_value=[])
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock(side_effect=added_objects.append)
    mock_session.flush = AsyncMock()

    result = await assign_memory(
        session=mock_session,
        handoff_record_id=handoff_id,
        memory_id=memory_id,
        new_owner_id=new_owner_id,
        departing_user_id=dep_user_id,
    )

    assert "attribution" in result
    # New attribution record was added
    from sourcemind.models.attribution import Attribution
    attr_objects = [o for o in added_objects if isinstance(o, Attribution)]
    assert len(attr_objects) == 1
    # Transfer amount = 40% of departing user's 80% = 32%
    assert abs(attr_objects[0].contribution_weight - 0.32) < 0.001


@pytest.mark.unit
@pytest.mark.asyncio
async def test_assignment_creates_attribution_record():
    """assign_memory must INSERT a new Attribution row for the new owner."""
    from sourcemind.services.attribution.handoff import assign_memory
    from sourcemind.models.attribution import Attribution

    added_objects = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        r = MagicMock()
        r.fetchone = MagicMock(return_value=(0.5,))
        r.fetchall = MagicMock(return_value=[])
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock(side_effect=added_objects.append)
    mock_session.flush = AsyncMock()

    await assign_memory(
        session=mock_session,
        handoff_record_id=uuid.uuid4(),
        memory_id=uuid.uuid4(),
        new_owner_id=uuid.uuid4(),
        departing_user_id=uuid.uuid4(),
    )

    attr_objects = [o for o in added_objects if isinstance(o, Attribution)]
    assert len(attr_objects) == 1
    assert attr_objects[0].trigger_action == "transfer"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_original_attribution_records_unchanged_after_assignment():
    """assign_memory must never UPDATE existing attribution records."""
    from sourcemind.services.attribution.handoff import assign_memory

    update_calls = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        # Detect any UPDATE on attributions table
        if "UPDATE attributions" in stmt_str.upper():
            update_calls.append(stmt_str)
        r = MagicMock()
        r.fetchone = MagicMock(return_value=(0.7,))
        r.fetchall = MagicMock(return_value=[])
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    await assign_memory(
        session=mock_session,
        handoff_record_id=uuid.uuid4(),
        memory_id=uuid.uuid4(),
        new_owner_id=uuid.uuid4(),
        departing_user_id=uuid.uuid4(),
    )

    assert len(update_calls) == 0, "Attribution records were updated (should be append-only)"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_departed_user_name_preserved_in_history():
    """
    complete_handoff sets status='departed' but does not modify attributions.
    Historical attribution records (with departing user's name) must remain.
    """
    from sourcemind.services.attribution.handoff import complete_handoff

    update_calls = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        if "UPDATE" in stmt_str:
            update_calls.append(stmt_str)
        r = MagicMock()
        r.scalar = MagicMock(return_value=0)
        r.fetchone = MagicMock(return_value=None)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.flush = AsyncMock()

    result = await complete_handoff(
        session=mock_session,
        workspace_id=uuid.uuid4(),
        departing_user_id=uuid.uuid4(),
        handoff_record_id=uuid.uuid4(),
    )

    assert result["status"] == "completed"
    # Only workspace_members and handoff_records should be updated
    attribution_updates = [c for c in update_calls if "attributions" in c.lower()]
    assert len(attribution_updates) == 0, "Attribution records were modified"
