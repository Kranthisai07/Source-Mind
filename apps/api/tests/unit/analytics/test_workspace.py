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

    # Main result: 2 contributors. Trailing column is collab_count, now
    # rolled into the same query instead of an N+1 per-contributor lookup.
    contributor_rows = [
        (uid1, "Alice", "alice@acme.dev", 10, 15, 0.6, "2025-03-10", 3),
        (uid2, "Bob", "bob@acme.dev", 5, 8, 0.4, "2025-03-08", 3),
    ]

    # get_contribution_map now runs a second query for the importance-weighted
    # project score, so the stub has to answer per query rather than returning
    # the same rows to every execute(). Its columns differ (5, not 8).
    project_rows = [
        (uid1, "Alice", 4.2, 15, 0.28),
        (uid2, "Bob", 1.1, 8, 0.14),
    ]

    async def execute_side_effect(stmt, params=None, **kwargs):
        r = MagicMock()
        rows = project_rows if "latest_attribution" in str(stmt) else contributor_rows
        r.fetchall = MagicMock(return_value=rows)
        return r

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    result = await get_contribution_map(mock_session, ws_id)
    assert len(result["contributors"]) == 2
    names = [c["name"] for c in result["contributors"]]
    assert "Alice" in names
    assert "Bob" in names

    # The aggregate rides along on the existing payload.
    alice = next(c for c in result["contributors"] if c["name"] == "Alice")
    assert alice["project_contribution_score"] == 4.2
    assert alice["avg_importance_of_their_memories"] == 0.28


# ─────────────────────────────────────────────────────────────────────────────
# Health score — the number shown on the dashboard
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_empty_workspace_scores_perfect_not_zero():
    """A workspace with nothing in it has no problems, and 0.0 would read as
    catastrophic on a brand-new workspace."""
    from sourcemind.services.analytics.workspace import _compute_health_score

    assert _compute_health_score(0, 0, 0, 0, 0, 0) == 1.0


@pytest.mark.unit
def test_healthy_workspace_scores_near_one():
    from sourcemind.services.analytics.workspace import _compute_health_score

    score = _compute_health_score(
        total_memories=100,
        single_contributor_count=0,      # nothing siloed
        updated_in_90_days=50,
        total_important=50,              # everything important is fresh
        open_conflicts=0,
        multi_contributor_count=100,     # everything collaborative
    )
    assert score == pytest.approx(1.0, abs=0.01)


@pytest.mark.unit
def test_worst_case_workspace_scores_near_zero():
    from sourcemind.services.analytics.workspace import _compute_health_score

    score = _compute_health_score(
        total_memories=100,
        single_contributor_count=100,    # everything siloed
        updated_in_90_days=0,            # nothing fresh
        total_important=50,
        open_conflicts=100,              # conflict everywhere
        multi_contributor_count=0,
    )
    assert score == pytest.approx(0.0, abs=0.01)


@pytest.mark.unit
def test_score_is_always_within_bounds():
    """Conflicts can exceed the memory count (many conflicts per memory), so
    the ratio must be clamped rather than allowed to drive the score negative.
    """
    from sourcemind.services.analytics.workspace import _compute_health_score

    score = _compute_health_score(
        total_memories=10,
        single_contributor_count=10,
        updated_in_90_days=0,
        total_important=1,
        open_conflicts=500,   # far more conflicts than memories
        multi_contributor_count=0,
    )
    assert 0.0 <= score <= 1.0


@pytest.mark.unit
def test_freshness_does_not_divide_by_zero_when_nothing_is_important():
    from sourcemind.services.analytics.workspace import _compute_health_score

    score = _compute_health_score(
        total_memories=10,
        single_contributor_count=0,
        updated_in_90_days=0,
        total_important=0,   # denominator guarded by max(..., 1)
        open_conflicts=0,
        multi_contributor_count=10,
    )
    assert 0.0 <= score <= 1.0


@pytest.mark.unit
def test_each_component_moves_the_score_in_the_right_direction():
    """Each of the four components must be able to lower the score on its own.

    A component wired with the wrong sign, or dropped from the sum, would
    otherwise be invisible — the score would still look plausible.
    """
    from sourcemind.services.analytics.workspace import _compute_health_score

    baseline = _compute_health_score(100, 0, 50, 50, 0, 100)

    worse_coverage = _compute_health_score(100, 50, 50, 50, 0, 100)
    worse_freshness = _compute_health_score(100, 0, 10, 50, 0, 100)
    worse_conflict = _compute_health_score(100, 0, 50, 50, 40, 100)
    worse_attribution = _compute_health_score(100, 0, 50, 50, 0, 20)

    assert worse_coverage < baseline, "single-contributor memories must hurt"
    assert worse_freshness < baseline, "stale important memories must hurt"
    assert worse_conflict < baseline, "open conflicts must hurt"
    assert worse_attribution < baseline, "low collaboration must hurt"


@pytest.mark.unit
def test_component_weights_match_the_documented_ratios():
    """coverage 30 / freshness 30 / conflict 25 / attribution 15.

    Measured by driving one component from best to worst and observing the
    drop, so the constants cannot be reshuffled without this failing.
    """
    from sourcemind.services.analytics.workspace import _compute_health_score

    best = _compute_health_score(100, 0, 50, 50, 0, 100)

    drop_coverage = best - _compute_health_score(100, 100, 50, 50, 0, 100)
    drop_freshness = best - _compute_health_score(100, 0, 0, 50, 0, 100)
    drop_conflict = best - _compute_health_score(100, 0, 50, 50, 100, 100)
    drop_attribution = best - _compute_health_score(100, 0, 50, 50, 0, 0)

    assert drop_coverage == pytest.approx(0.30, abs=0.01)
    assert drop_freshness == pytest.approx(0.30, abs=0.01)
    assert drop_conflict == pytest.approx(0.25, abs=0.01)
    assert drop_attribution == pytest.approx(0.15, abs=0.01)
