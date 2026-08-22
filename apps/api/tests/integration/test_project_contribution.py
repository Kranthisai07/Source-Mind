"""Project-level contribution score, against live Postgres.

This is an AGGREGATE view sitting on top of per-memory attribution. It does not
touch, reinterpret or re-derive the 5-signal algorithm in
services/attribution/scorer.py — that stays exactly as it is.

    ProjectContribution(c) = SUM over m attributed to c of
                             contribution_weight(c, m) * importance_score(m)

The point of the weighting is that volume alone should not decide standing.
Someone who wrote the one memory everything else derives from should outrank
someone who made many incidental edits — and the multiplier doing that work is
importance_score, which the system already computed, so this metric introduces
no judgement of its own about what matters.

importance_score is SET DIRECTLY here rather than grown through relations and
approvals. What is under test is the aggregation, and driving importance
through the real pipeline would make these tests depend on the importance
algorithm's behaviour instead of this one's.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.services.analytics.workspace import compute_project_contribution


async def _user(session: AsyncSession, name: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:10]
    await session.execute(
        text(
            "INSERT INTO users (id, clerk_id, email, display_name) VALUES "
            "(CAST(:id AS uuid), :clerk, :email, :name)"
        ),
        {
            "id": str(user_id),
            "clerk": f"clerk-{suffix}",
            "email": f"{suffix}@t.local",
            "name": name,
        },
    )
    return user_id


async def _memory(
    session: AsyncSession, workspace_id, *, importance: float
) -> uuid.UUID:
    memory_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memories (id, workspace_id, content, content_hash, "
            "version, current_version, importance_score) VALUES "
            "(CAST(:id AS uuid), CAST(:ws AS uuid), :content, :hash, 1, TRUE, :imp)"
        ),
        {
            "id": str(memory_id),
            "ws": str(workspace_id),
            "content": f"memory {memory_id}",
            "hash": uuid.uuid4().hex,
            "imp": importance,
        },
    )
    return memory_id


async def _attribute(
    session: AsyncSession, memory_id, user_id, weight: float
) -> None:
    await session.execute(
        text(
            "INSERT INTO attributions (memory_id, user_id, contribution_weight, "
            "trigger_action) VALUES (CAST(:m AS uuid), CAST(:u AS uuid), :w, 'create')"
        ),
        {"m": str(memory_id), "u": str(user_id), "w": weight},
    )


def _score_for(rows: list[dict], user_id: uuid.UUID) -> float:
    for row in rows:
        if row["contributor_id"] == str(user_id):
            return row["project_contribution_score"]
    raise AssertionError(f"{user_id} missing from project contribution rows")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_one_important_memory_outranks_five_trivial_ones(
    db_session, test_workspace
):
    """The case the metric exists for.

    A wrote one memory that matters. B wrote five that do not. Counting
    memories makes B look like the bigger contributor by 5x; weighting each by
    the importance the system already assigned reverses that.
    """
    author_a = await _user(db_session, "Contributor A")
    author_b = await _user(db_session, "Contributor B")

    important = await _memory(db_session, test_workspace.id, importance=0.9)
    await _attribute(db_session, important, author_a, 1.0)

    for _ in range(5):
        trivial = await _memory(db_session, test_workspace.id, importance=0.1)
        await _attribute(db_session, trivial, author_b, 1.0)

    await db_session.flush()
    rows = await compute_project_contribution(db_session, test_workspace.id)

    score_a = _score_for(rows, author_a)
    score_b = _score_for(rows, author_b)

    # 1 x 1.0 x 0.9 = 0.9   vs   5 x 1.0 x 0.1 = 0.5
    assert score_a == pytest.approx(0.9)
    assert score_b == pytest.approx(0.5)
    assert score_a > score_b, (
        f"one high-importance memory ({score_a}) must outrank five trivial "
        f"ones ({score_b})"
    )

    # And the raw counts still say the opposite, which is the whole point.
    by_id = {r["contributor_id"]: r for r in rows}
    assert by_id[str(author_a)]["memory_count"] == 1
    assert by_id[str(author_b)]["memory_count"] == 5

    # Ordering is by score, so A leads despite owning fewer memories.
    assert rows[0]["contributor_id"] == str(author_a)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_volume_still_wins_when_importance_is_equal(
    db_session, test_workspace
):
    """The formula must de-emphasise volume, never penalise it.

    Hold importance equal and the contributor with more memories has to score
    higher. Without this, a formula that simply inverted the volume bias would
    pass the test above while being just as wrong.
    """
    fewer = await _user(db_session, "Contributor C")
    more = await _user(db_session, "Contributor D")

    for _ in range(2):
        m = await _memory(db_session, test_workspace.id, importance=0.4)
        await _attribute(db_session, m, fewer, 1.0)
    for _ in range(5):
        m = await _memory(db_session, test_workspace.id, importance=0.4)
        await _attribute(db_session, m, more, 1.0)

    await db_session.flush()
    rows = await compute_project_contribution(db_session, test_workspace.id)

    score_fewer = _score_for(rows, fewer)   # 2 x 0.4 = 0.8
    score_more = _score_for(rows, more)     # 5 x 0.4 = 2.0

    assert score_fewer == pytest.approx(0.8)
    assert score_more == pytest.approx(2.0)
    assert score_more > score_fewer, (
        "with importance held equal, more contributions must score higher"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_partial_attribution_is_split_not_duplicated(
    db_session, test_workspace
):
    """Two contributors sharing one memory split its importance by weight."""
    first = await _user(db_session, "Contributor E")
    second = await _user(db_session, "Contributor F")

    shared = await _memory(db_session, test_workspace.id, importance=1.0)
    await _attribute(db_session, shared, first, 0.75)
    await _attribute(db_session, shared, second, 0.25)

    await db_session.flush()
    rows = await compute_project_contribution(db_session, test_workspace.id)

    assert _score_for(rows, first) == pytest.approx(0.75)
    assert _score_for(rows, second) == pytest.approx(0.25)
    # The memory's importance is distributed, never counted once per person.
    assert sum(r["project_contribution_score"] for r in rows) == pytest.approx(1.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_append_only_history_does_not_inflate_the_score(
    db_session, test_workspace
):
    """Only the latest attribution row per (memory, contributor) counts.

    `attributions` is append-only — enforced by a DB trigger — so recomputing a
    memory's attribution leaves the earlier rows in place. Summing rows
    directly would count a contributor's share once per historical row, so
    someone whose memory was recomputed often would climb the ranking purely
    for having been recomputed. This is why the query takes DISTINCT ON the
    latest row first.
    """
    author = await _user(db_session, "Contributor G")
    memory = await _memory(db_session, test_workspace.id, importance=0.8)

    # Three generations of the same attribution, oldest first. Only the last
    # reflects what the scorer currently believes.
    for weight in (0.4, 0.6, 1.0):
        await db_session.execute(
            text(
                "INSERT INTO attributions (memory_id, user_id, "
                "contribution_weight, trigger_action, created_at) VALUES "
                "(CAST(:m AS uuid), CAST(:u AS uuid), :w, 'edit', NOW() + "
                "make_interval(secs => :offset))"
            ),
            {
                "m": str(memory),
                "u": str(author),
                "w": weight,
                "offset": weight * 10,
            },
        )
    await db_session.flush()

    rows = await compute_project_contribution(db_session, test_workspace.id)
    score = _score_for(rows, author)

    # Latest weight 1.0 x importance 0.8. Summing all three rows would give
    # (0.4 + 0.6 + 1.0) x 0.8 = 1.6 — double the truth.
    assert score == pytest.approx(0.8), (
        f"expected only the latest attribution to count, got {score}"
    )
    assert _score_for(rows, author) != pytest.approx(1.6)
    by_id = {r["contributor_id"]: r for r in rows}
    assert by_id[str(author)]["memory_count"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_contribution_map_exposes_the_score(db_session, test_workspace):
    """The aggregate rides on the existing endpoint, not a parallel one."""
    from sourcemind.services.analytics.workspace import get_contribution_map

    author = await _user(db_session, "Contributor H")
    memory = await _memory(db_session, test_workspace.id, importance=0.6)
    await _attribute(db_session, memory, author, 1.0)
    await db_session.flush()

    payload = await get_contribution_map(db_session, test_workspace.id)
    entry = next(
        c for c in payload["contributors"] if c["user_id"] == str(author)
    )

    assert entry["project_contribution_score"] == pytest.approx(0.6)
    assert entry["avg_importance_of_their_memories"] == pytest.approx(0.6)
    # The fields the endpoint already returned must survive the addition.
    assert "total_memories_influenced" in entry
    assert "collaboration_rate" in entry
