"""Tests for services/conflict/severity.py.

Severity ranks attention; it must never decide an outcome. The strongest
effect it has is blocks_derivation, and even that only marks a conflict as
blocking — a human still resolves it (ADR-008, AI advisory-only).

Ladder:
    critical  importance > 0.7
              (claim count is a secondary signal only — it can lift low to
               medium, but is never required for critical)
    medium    importance > 0.4 OR  claims >= 2
    low       everything else
"""

from __future__ import annotations

import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sourcemind.services.conflict.severity import (
    classify_severity,
    compute_conflict_severity,
    recompute_severity_for_memory,
)


def _session(
    *,
    importance: float | None,
    disputing: int,
    status: str = "open",
    conflict_exists: bool = True,
):
    """Session stubbing the three reads compute_conflict_severity performs."""
    mem_a, mem_b = uuid.uuid4(), uuid.uuid4()
    writes: list[dict] = []

    async def execute(stmt, params=None, **kwargs):
        s = str(stmt)
        r = MagicMock()
        if "SELECT memory_a_id" in s:
            r.first = MagicMock(
                return_value=(str(mem_a), str(mem_b), status)
                if conflict_exists
                else None
            )
        elif "importance_score" in s:
            r.scalar = MagicMock(return_value=importance)
        elif "COUNT(DISTINCT other)" in s:
            r.scalar = MagicMock(return_value=disputing)
        elif "UPDATE memory_conflicts" in s:
            writes.append(params or {})
        return r

    sess = AsyncMock()
    sess.execute = AsyncMock(side_effect=execute)
    sess.flush = AsyncMock()
    sess._writes = writes  # type: ignore[attr-defined]
    return sess


# ─── the ladder, in isolation ────────────────────────────────────────────────

@pytest.mark.unit
def test_severity_critical_high_importance_many_claims():
    assert classify_severity(0.9, 3) == "critical"
    assert classify_severity(0.71, 5) == "critical"


@pytest.mark.unit
def test_severity_critical_two_claims_high_importance():
    """Two people disagreeing about an important decision is critical.

    This was unreachable before: critical demanded three competing claims,
    and an 'updates' verdict retires the disputed memory so it can never
    collect a third through ingestion. It is the ordinary case, not an edge.
    """
    assert classify_severity(0.75, 2) == "critical"
    assert classify_severity(0.71, 2) == "critical"


@pytest.mark.unit
def test_claim_count_is_never_required_for_critical():
    """A single claim on a high-importance memory is still critical."""
    assert classify_severity(0.9, 1) == "critical"
    assert classify_severity(0.9, 0) == "critical"


@pytest.mark.unit
def test_severity_medium_moderate_importance():
    """Importance alone can reach medium, regardless of claim count."""
    assert classify_severity(0.5, 1) == "medium"
    assert classify_severity(0.41, 1) == "medium"


@pytest.mark.unit
def test_severity_medium_many_claims_low_importance():
    """Claims alone can reach medium, regardless of importance."""
    assert classify_severity(0.1, 2) == "medium"
    assert classify_severity(0.0, 9) == "medium"


@pytest.mark.unit
def test_severity_low_default_case():
    assert classify_severity(0.4, 1) == "low"
    assert classify_severity(0.0, 0) == "low"
    assert classify_severity(0.0, 1) == "low"


@pytest.mark.unit
@pytest.mark.parametrize(
    "importance,claims,expected",
    [
        (0.7, 3, "medium"),     # boundary is strict >, not >=
        (0.71, 2, "critical"),  # importance alone is now sufficient
        (0.9, 2, "critical"),   # claim count is not a requirement
        (0.4, 2, "medium"),    # claims carry it
        (0.4, 1, "low"),       # neither clause met
    ],
)
def test_ladder_boundaries_are_exact(importance, claims, expected):
    assert classify_severity(importance, claims) == expected


# ─── persistence ─────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_computed_severity_is_written_back_with_the_claim_count():
    # 2 other memories disputing -> claim count 3
    sess = _session(importance=0.9, disputing=2)
    severity = await compute_conflict_severity(sess, uuid.uuid4())

    assert severity == "critical"
    assert len(sess._writes) == 1
    written = sess._writes[0]
    assert written["sev"] == "critical"
    assert written["cnt"] == 3, "claim count is disputing memories plus the memory itself"
    assert written["blk"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocks_derivation_true_only_for_critical():
    cases = [
        (0.9, 2, "critical", True),
        (0.9, 1, "critical", True),   # one rival, high importance
        (0.5, 0, "medium", False),
        (0.1, 0, "low", False),
    ]
    for importance, disputing, expected_sev, expected_block in cases:
        sess = _session(importance=importance, disputing=disputing)
        sev = await compute_conflict_severity(sess, uuid.uuid4())
        assert sev == expected_sev
        assert sess._writes[0]["blk"] is expected_block, (
            f"blocks_derivation must be {expected_block} for {expected_sev}"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolved_conflict_never_blocks_even_if_critical():
    """A settled disagreement must not hold up derivation."""
    sess = _session(importance=0.95, disputing=5, status="resolved")
    sev = await compute_conflict_severity(sess, uuid.uuid4())
    assert sev == "critical", "severity is retained as a record of seriousness"
    assert sess._writes[0]["blk"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_conflict_returns_empty_and_writes_nothing():
    sess = _session(importance=0.9, disputing=3, conflict_exists=False)
    assert await compute_conflict_severity(sess, uuid.uuid4()) == ""
    assert sess._writes == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_null_importance_is_treated_as_zero_not_an_error():
    """importance_score can be NULL on a freshly ingested memory."""
    sess = _session(importance=None, disputing=0)
    assert await compute_conflict_severity(sess, uuid.uuid4()) == "low"


# ─── staleness: recompute on importance change ───────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_severity_recomputed_when_importance_changes():
    """Every unresolved conflict on the memory is rescored, so severity cannot
    drift behind the importance_score it is derived from."""
    conflict_ids = [str(uuid.uuid4()) for _ in range(3)]
    seen: list[str] = []

    async def execute(stmt, params=None, **kwargs):
        s = str(stmt)
        r = MagicMock()
        if "SELECT id::text FROM memory_conflicts" in s:
            r.fetchall = MagicMock(return_value=[(c,) for c in conflict_ids])
        elif "SELECT memory_a_id" in s:
            seen.append(params["cid"])
            r.first = MagicMock(
                return_value=(str(uuid.uuid4()), str(uuid.uuid4()), "open")
            )
        elif "importance_score" in s:
            r.scalar = MagicMock(return_value=0.2)
        elif "COUNT(DISTINCT other)" in s:
            r.scalar = MagicMock(return_value=0)
        return r

    sess = AsyncMock()
    sess.execute = AsyncMock(side_effect=execute)
    sess.flush = AsyncMock()

    count = await recompute_severity_for_memory(sess, uuid.uuid4())
    assert count == 3
    assert sorted(seen) == sorted(conflict_ids), "every conflict must be rescored"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recompute_is_a_no_op_when_the_memory_has_no_conflicts():
    sess = AsyncMock()
    r = MagicMock()
    r.fetchall = MagicMock(return_value=[])
    sess.execute = AsyncMock(return_value=r)
    assert await recompute_severity_for_memory(sess, uuid.uuid4()) == 0


# ─── the invariant that matters most ─────────────────────────────────────────

@pytest.mark.unit
def test_severity_module_never_resolves_a_conflict():
    """No severity path may set status, resolver_id or resolved_at.

    Severity ranks attention. Auto-resolution at any severity is forbidden;
    this fails if a future change tries to introduce it.
    """
    src = (
        pathlib.Path(__file__).resolve().parents[3]
        / "sourcemind"
        / "services"
        / "conflict"
        / "severity.py"
    )
    body = src.read_text(encoding="utf-8")
    for forbidden in ("SET status", "resolver_id", "resolved_at"):
        assert forbidden not in body, (
            f"severity.py must never resolve a conflict, found {forbidden!r}"
        )
