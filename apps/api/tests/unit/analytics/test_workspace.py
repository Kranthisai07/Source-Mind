"""Unit tests for workspace analytics service."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_scalar_session(*scalars):
    """Session where each execute() returns the next scalar value."""
    mock_session = AsyncMock()
    call_index = [0]

    async def execute_side_effect(stmt, params=None, **kwargs):
        idx = call_index[0]
        call_index[0] += 1
        r = MagicMock()
        if idx < len(scalars):
            val = scalars[idx]
            if isinstance(val, list):
                r.fetchall = MagicMock(return_value=val)
                r.fetchone = MagicMock(return_value=val[0] if val else None)
                r.scalar = MagicMock(return_value=val[0] if val else 0)
            elif isinstance(val, tuple):
                r.fetchone = MagicMock(return_value=val)
                r.scalar = MagicMock(return_value=val[0] if val else 0)
            else:
                r.scalar = MagicMock(return_value=val)
                r.fetchone = MagicMock(return_value=(val,))
                r.fetchall = MagicMock(return_value=[(val,)])
        else:
            r.scalar = MagicMock(return_value=0)
            r.fetchone = MagicMock(return_value=(0,))
            r.fetchall = MagicMock(return_value=[])
        return r

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    return mock_session


@pytest.mark.unit
def test_health_score_perfect_workspace():
    """A workspace with all signals at ideal values should score 1.0."""
    from sourcemind.services.analytics.workspace import _compute_health_score

    score = _compute_health_score(
        total_memories=100,
        single_contributor_count=0,   # all multi-contributor
        updated_in_90_days=100,       # all recent
        total_important=100,
        open_conflicts=0,             # no conflicts
        multi_contributor_count=100,  # all multi-contributor
    )
    assert abs(score - 1.0) < 0.001


@pytest.mark.unit
def test_health_score_single_contributor_workspace():
    """A workspace where every memory has only one contributor should score lower."""
    from sourcemind.services.analytics.workspace import _compute_health_score

    score = _compute_health_score(
        total_memories=100,
        single_contributor_count=100,  # all single-contributor
        updated_in_90_days=50,
        total_important=100,
        open_conflicts=10,
        multi_contributor_count=0,
    )
    # coverage = 0.0, freshness = 0.5, conflict = 0.9, attribution = 0.0
    # = 0*0.30 + 0.5*0.30 + 0.9*0.25 + 0*0.15 = 0.15 + 0.225 = 0.375
    assert score < 0.5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gap_detection_finds_single_contributor_memories():
    """get_knowledge_gaps should detect single_contributor gaps."""
    from sourcemind.services.analytics.workspace import get_knowledge_gaps

    ws_id = uuid.uuid4()

    # Return: single_contrib=5, stale=0, conflict_tags=[]
    mock_session = _make_scalar_session(
        5,    # single_contributor count
        0,    # stale count
        [],   # high_conflict_area tags
    )

    result = await get_knowledge_gaps(mock_session, ws_id)
    gap_types = [g["gap_type"] for g in result["gaps"]]
    assert "single_contributor" in gap_types


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gap_detection_finds_stale_important_memories():
    """get_knowledge_gaps should detect no_recent_update gaps."""
    from sourcemind.services.analytics.workspace import get_knowledge_gaps

    ws_id = uuid.uuid4()

    # Return: single_contrib=0, stale=3, conflict_tags=[]
    mock_session = _make_scalar_session(
        0,    # single_contributor count
        3,    # stale important memories
        [],   # no high-conflict tags
    )

    result = await get_knowledge_gaps(mock_session, ws_id)
    gap_types = [g["gap_type"] for g in result["gaps"]]
    assert "no_recent_update" in gap_types


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contribution_map_includes_all_active_contributors():
    """get_contribution_map should return one entry per contributor."""
    from sourcemind.services.analytics.workspace import get_contribution_map

    ws_id = uuid.uuid4()
    uid1, uid2 = str(uuid.uuid4()), str(uuid.uuid4())

    # Main result: 2 contributors
    contributor_rows = [
        (uid1, "Alice", "alice@acme.dev", 10, 15, 0.6, "2025-03-10"),
        (uid2, "Bob", "bob@acme.dev", 5, 8, 0.4, "2025-03-08"),
    ]

    call_index = [0]

    async def execute_side_effect(stmt, params=None, **kwargs):
        call_index[0] += 1
        r = MagicMock()
        if call_index[0] == 1:
            r.fetchall = MagicMock(return_value=contributor_rows)
        else:
            # Collaboration rate queries
            r.scalar = MagicMock(return_value=3)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    result = await get_contribution_map(mock_session, ws_id)
    assert len(result["contributors"]) == 2
    names = [c["name"] for c in result["contributors"]]
    assert "Alice" in names
    assert "Bob" in names
