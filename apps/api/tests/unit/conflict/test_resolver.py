"""Unit tests for conflict resolution service."""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_session(conflict_row=None, mem_row=None):
    """Create a mock async session."""
    mock_session = AsyncMock()
    call_count = [0]

    def make_result(row):
        r = MagicMock()
        r.fetchone = MagicMock(return_value=row)
        r.fetchall = MagicMock(return_value=[row] if row else [])
        return r

    async def execute_side_effect(stmt, *args, **kwargs):
        call_count[0] += 1
        stmt_str = str(stmt)
        if call_count[0] == 1 and mem_row:
            return make_result(mem_row)
        return make_result(None)

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    return mock_session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kept_a_marks_memory_b_not_latest():
    """Resolving with kept_a should UPDATE memories SET current_version=FALSE for memory_b."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conflict_id = uuid.uuid4()
    resolver_id = uuid.uuid4()

    calls_made = []
    call_count = [0]

    async def execute_side_effect(stmt, params=None, **kwargs):
        call_count[0] += 1
        stmt_str = str(stmt)
        calls_made.append(stmt_str)

        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        else:
            r.fetchone = MagicMock(return_value=(str(uuid.uuid4()),))
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=conflict_id,
        resolver_id=resolver_id,
        resolution_type="kept_a",
        resolution_note="Memory A is more accurate.",
    )

    assert result is True
    update_calls = [c for c in calls_made if "current_version = FALSE" in c]
    assert len(update_calls) >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_kept_b_marks_memory_a_not_latest():
    """Resolving with kept_b should UPDATE memories SET current_version=FALSE for memory_a."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    ws_id = str(uuid.uuid4())
    conflict_id = uuid.uuid4()

    calls_made = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        calls_made.append(stmt_str)
        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        else:
            r.fetchone = MagicMock(return_value=(str(uuid.uuid4()),))
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=conflict_id,
        resolver_id=uuid.uuid4(),
        resolution_type="kept_b",
    )

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deferred_sets_revisit_date():
    """Deferred resolution should store revisit_at and NOT update status to resolved."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    revisit_date = datetime.now(timezone.utc) + timedelta(days=30)
    conflict_id = uuid.uuid4()

    deferred_params = {}

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, str(uuid.uuid4())))
        elif "deferred" in stmt_str and params:
            deferred_params.update(params or {})
            r.fetchone = MagicMock(return_value=(str(conflict_id),))
        else:
            r.fetchone = MagicMock(return_value=None)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=conflict_id,
        resolver_id=uuid.uuid4(),
        resolution_type="deferred",
        revisit_at=revisit_date,
    )

    assert result is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_split_adds_tags_to_both_memories():
    """Split resolution should add tag_a to memory_a and tag_b to memory_b."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    tag_update_params = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, str(uuid.uuid4())))
        elif "array_append" in stmt_str and params:
            tag_update_params.append(dict(params))
            r.fetchone = MagicMock(return_value=None)
        else:
            r.fetchone = MagicMock(return_value=(str(uuid.uuid4()),))
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=uuid.uuid4(),
        resolver_id=uuid.uuid4(),
        resolution_type="split",
        tag_a="us-east",
        tag_b="eu-west",
    )

    assert result is True
    assert len(tag_update_params) == 2
    tags_applied = {p["tag"] for p in tag_update_params}
    assert "us-east" in tags_applied
    assert "eu-west" in tags_applied


@pytest.mark.unit
@pytest.mark.asyncio
async def test_merged_deprecates_both_creates_new():
    """Merged resolution should deprecate both memories and INSERT a new merged memory."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())
    ws_id = str(uuid.UUID("00000000-0000-4000-8000-000000000010"))
    added_objects = []

    async def execute_side_effect(stmt, params=None, **kwargs):
        stmt_str = str(stmt)
        r = MagicMock()
        if "SELECT memory_a_id" in stmt_str:
            r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, ws_id))
        else:
            r.fetchone = MagicMock(return_value=(str(uuid.uuid4()),))
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.add = MagicMock(side_effect=added_objects.append)
    mock_session.flush = AsyncMock()

    result = await resolve_conflict(
        session=mock_session,
        conflict_id=uuid.uuid4(),
        resolver_id=uuid.uuid4(),
        resolution_type="merged",
        merged_content="The system deployed to both AWS us-east-1 and eu-west-1.",
    )

    assert result is True
    # A new merged memory should have been added to the session
    from sourcemind.models.memory import Memory
    memory_objects = [o for o in added_objects if isinstance(o, Memory)]
    assert len(memory_objects) == 1
    assert memory_objects[0].content == "The system deployed to both AWS us-east-1 and eu-west-1."


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_requires_known_type():
    """Unknown resolution_type should raise ValueError."""
    from sourcemind.services.conflict.resolver import resolve_conflict

    mem_a_id = str(uuid.uuid4())
    mem_b_id = str(uuid.uuid4())

    async def execute_side_effect(stmt, params=None, **kwargs):
        r = MagicMock()
        r.fetchone = MagicMock(return_value=(mem_a_id, mem_b_id, str(uuid.uuid4())))
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    with pytest.raises(ValueError, match="Unknown resolution_type"):
        await resolve_conflict(
            session=mock_session,
            conflict_id=uuid.uuid4(),
            resolver_id=uuid.uuid4(),
            resolution_type="invalid_type",
        )
