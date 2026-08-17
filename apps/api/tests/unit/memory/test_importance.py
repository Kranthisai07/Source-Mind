"""Tests for services/memory/importance.py.

The importance score is not cosmetic: handoff tiering thresholds on it
(>0.8 critical, >0.5 important), the no_recent_update gap detector filters on
>0.7, and the health score's freshness component filters on >0.5. A wrong
score silently misroutes knowledge transfer when someone leaves.

Weights (ADR): inbound relations 35%, approvals 25%, versions 20%,
recency 10%, category 10%.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sourcemind.services.memory.importance import (
    _s1_inbound_relations,
    _s2_approval_count,
    _s3_version_count,
    _s4_recency,
    _s5_category,
    compute_importance_score,
)


# ─── individual signals ──────────────────────────────────────────────────────

@pytest.mark.unit
def test_s1_is_zero_with_no_inbound_relations_and_rises_monotonically():
    assert _s1_inbound_relations(0) == 0.0
    values = [_s1_inbound_relations(n) for n in range(0, 20)]
    assert values == sorted(values), "more references must never lower importance"


@pytest.mark.unit
def test_s1_saturates_rather_than_exceeding_one():
    """Logarithmic, so a heavily-referenced memory cannot dominate the score."""
    assert _s1_inbound_relations(16) == pytest.approx(1.0, abs=0.01)
    assert _s1_inbound_relations(1000) <= 1.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "approvals,expected",
    [(0, 0.0), (1, 0.2), (5, 1.0), (50, 1.0)],
)
def test_s2_scales_to_five_approvals_then_caps(approvals, expected):
    assert _s2_approval_count(approvals) == pytest.approx(expected)


@pytest.mark.unit
def test_s3_treats_an_unedited_memory_as_low_but_nonzero():
    """version=1 means nobody revisited it, but it is still real knowledge."""
    assert _s3_version_count(0) == 0.1
    assert _s3_version_count(1) == 0.1
    assert _s3_version_count(8) == pytest.approx(1.0, abs=0.01)


@pytest.mark.unit
def test_s3_rises_with_revision_count():
    assert _s3_version_count(4) > _s3_version_count(2) > _s3_version_count(1)


@pytest.mark.unit
def test_s4_decays_with_age():
    now = datetime.now(timezone.utc)
    fresh = _s4_recency(now)
    month = _s4_recency(now - timedelta(days=30))
    year = _s4_recency(now - timedelta(days=365))
    assert fresh > month > year
    assert fresh == pytest.approx(1.0, abs=0.01)
    assert 0.0 <= year < 0.1


@pytest.mark.unit
@pytest.mark.parametrize(
    "category,expected",
    [
        ("decision", 1.0),
        ("process", 0.9),
        ("definition", 0.7),
        ("fact", 0.5),
        ("question", 0.3),
    ],
)
def test_s5_ranks_known_categories(category, expected):
    assert _s5_category(category) == expected


@pytest.mark.unit
@pytest.mark.parametrize("category", [None, "", "unrecognised-category"])
def test_s5_falls_back_to_neutral_for_unknown_categories(category):
    """This default is currently the *only* value in production, because
    nothing sets a category yet — see the note in store_memories."""
    assert _s5_category(category) == 0.5


# ─── composite ───────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_score_stays_within_bounds_at_both_extremes():
    now = datetime.now(timezone.utc)
    lowest = compute_importance_score(
        inbound_count=0, approval_count=0, version_count=1,
        last_updated_at=now - timedelta(days=3650), category="question",
    )
    highest = compute_importance_score(
        inbound_count=100, approval_count=100, version_count=100,
        last_updated_at=now, category="decision",
    )
    assert 0.0 <= lowest <= 1.0
    assert 0.0 <= highest <= 1.0
    assert highest > lowest


@pytest.mark.unit
def test_inbound_relations_outweigh_recency():
    """S1 carries 35% and S4 only 10%, so a well-referenced old memory must
    beat a fresh unreferenced one. Handoff tiering depends on this ordering.
    """
    now = datetime.now(timezone.utc)
    referenced_but_old = compute_importance_score(
        inbound_count=12, approval_count=0, version_count=1,
        last_updated_at=now - timedelta(days=200), category=None,
    )
    fresh_but_isolated = compute_importance_score(
        inbound_count=0, approval_count=0, version_count=1,
        last_updated_at=now, category=None,
    )
    assert referenced_but_old > fresh_but_isolated


@pytest.mark.unit
def test_score_is_deterministic():
    args = dict(
        inbound_count=3, approval_count=2, version_count=2,
        last_updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        category="process",
    )
    assert compute_importance_score(**args) == compute_importance_score(**args)


@pytest.mark.unit
def test_a_typical_critical_memory_clears_the_tier_1_threshold():
    """Sanity-check the scale against the threshold that actually uses it.

    Tier 1 handoff requires > 0.8. If the weighting drifted such that nothing
    realistic could reach that, tier 1 would quietly become empty.
    """
    score = compute_importance_score(
        inbound_count=16, approval_count=5, version_count=8,
        last_updated_at=datetime.now(timezone.utc), category="decision",
    )
    assert score > 0.8, f"a maximally important memory scored only {score}"
