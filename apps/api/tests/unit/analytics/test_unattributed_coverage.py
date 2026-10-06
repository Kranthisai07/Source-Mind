"""Coverage counts unattributed memories as missing (D-021).

Before D-021 ``coverage = 1 - single/total`` only penalised single-contributor
memories, so a memory with NO attribution row (an unresolved GitHub author)
counted as covered, and an unresolved memory that was later edited counted
only as an ordinary single-contributor memory. ``unattributed_memories`` is
current-version memories with no attribution rows OR an unresolved originating
author (derived from the document-level artifact link); it joins the coverage
penalty without double counting, and never raises coverage.

The SQL semantics are proven against PostgreSQL in
tests/integration/test_github_author_links_real_db.py; this file proves the
arithmetic and the response plumbing.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Four current-version memories:
#   m1 two contributors (resolved)            -> multi
#   m2 one contributor  (resolved)            -> single
#   m3 zero rows        (unresolved GitHub)   -> unattributed
#   m4 one editor row   (unresolved, edited)  -> unattributed (NOT single)
TOTAL = 4
MULTI = 1
OLD_SINGLE = 2  # what the pre-D-021 single-contributor query returns (m2, m4)
SINGLE_ONLY = 1
UNATTRIBUTED = 2


def _session() -> AsyncMock:
    async def execute(stmt, params=None, **_k):
        sql = " ".join(str(stmt).split())
        r = MagicMock()
        if "AS total_memories" in sql:
            r.fetchone.return_value = (TOTAL, 3)
        elif "unattributed" in sql:
            r.fetchone.return_value = SimpleNamespace(
                single_only=SINGLE_ONLY, unattributed=UNATTRIBUTED
            )
        elif "HAVING COUNT(DISTINCT a.user_id) = 1" in sql:
            r.scalar.return_value = OLD_SINGLE
        elif "HAVING COUNT(DISTINCT a.user_id) > 1" in sql:
            r.scalar.return_value = MULTI
        elif "importance_score > 0.5" in sql:
            r.fetchone.return_value = (0, 0)
        elif "memory_conflicts" in sql or "INTERVAL '30 days'" in sql:
            r.scalar.return_value = 0
        else:
            r.fetchall.return_value = []
        return r

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute)
    return session


async def _overview():
    from sourcemind.services.analytics.workspace import get_overview

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    with patch("sourcemind.core.redis_client.get_redis", return_value=redis):
        return await get_overview(_session(), uuid.uuid4())


@pytest.mark.unit
async def test_overview_exposes_unattributed_memories() -> None:
    result = await _overview()

    assert result["unattributed_memories"] == UNATTRIBUTED
    assert result["health_breakdown"]["unattributed_memories"] == UNATTRIBUTED


@pytest.mark.unit
async def test_zero_row_and_unresolved_edited_memories_count_as_missing() -> None:
    result = await _overview()

    # 1 - (single_only + unattributed) / total, each memory counted once.
    expected = round(1.0 - (SINGLE_ONLY + UNATTRIBUTED) / TOTAL, 4)
    assert result["health_breakdown"]["coverage"] == expected
    old_coverage = round(1.0 - OLD_SINGLE / TOTAL, 4)
    assert result["health_breakdown"]["coverage"] < old_coverage
    # attribution = multi/total is unchanged
    assert result["health_breakdown"]["attribution"] == round(MULTI / TOTAL, 4)


@pytest.mark.unit
def test_unattributed_memories_lower_the_health_score() -> None:
    from sourcemind.services.analytics.workspace import _compute_health_score

    baseline = _compute_health_score(4, 1, 0, 0, 0, 1)
    with_missing = _compute_health_score(4, 1, 0, 0, 0, 1, unattributed_count=2)

    assert with_missing < baseline
    assert with_missing == pytest.approx(baseline - 0.30 * 2 / 4, abs=1e-4)


@pytest.mark.unit
@pytest.mark.parametrize("single,unattributed", [(0, 0), (3, 1), (0, 4), (2, 2)])
def test_unattributed_never_raises_coverage(single: int, unattributed: int) -> None:
    from sourcemind.services.analytics.workspace import _compute_health_score

    without = _compute_health_score(4, single, 0, 0, 0, 0)
    with_missing = _compute_health_score(4, single, 0, 0, 0, 0, unattributed_count=unattributed)

    assert with_missing <= without
    assert 0.0 <= with_missing <= 1.0
