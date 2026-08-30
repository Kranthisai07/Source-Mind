"""Attribution scoring through the real functions, against live Postgres.

create_initial_attribution() writes what the 5-signal scorer computes rather
than a hardcoded copy of its output. On a first creation that changes exactly
one persisted value — structural_score, which was previously always 1.0 even
for content containing no entities.

The second test is the one that exercises the algorithm rather than its
degenerate case: a second contributor edits an ingested memory and the stored
weights genuinely split.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.services.attribution.engine import (
    create_initial_attribution,
    recompute_attribution,
)

ENTITY_RICH = "We migrated the API from PostgreSQL to Neo4j using Kubernetes in v2.1."
ENTITY_FREE = "the team discussed it yesterday and agreed to move on quietly"


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


async def _memory(session: AsyncSession, workspace_id, content: str) -> uuid.UUID:
    memory_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memories (id, workspace_id, content, content_hash, version, "
            "current_version) VALUES (CAST(:id AS uuid), CAST(:ws AS uuid), :content, "
            ":hash, 1, TRUE)"
        ),
        {
            "id": str(memory_id),
            "ws": str(workspace_id),
            "content": content,
            "hash": uuid.uuid4().hex,
        },
    )
    await session.flush()
    return memory_id


async def _latest_attributions(session: AsyncSession, memory_id) -> dict:
    """Newest attribution row per contributor.

    The table is append-only, so a recompute leaves the earlier rows in place
    and DISTINCT ON is needed to read what the system currently believes.
    """
    # Ordered by edit_position, not created_at. created_at defaults to NOW(),
    # which in Postgres is TRANSACTION start time, so every row written in one
    # transaction shares a timestamp and cannot be ordered against its
    # siblings. Reading by created_at here returned the creator's original
    # 1.0 row alongside the editor's recomputed one, summing to 1.44.
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (a.user_id) a.user_id::text, "
                "  a.contribution_weight, a.char_diff_score, a.semantic_score, "
                "  a.temporal_score, a.structural_score, a.approval_score "
                "FROM attributions a "
                "LEFT JOIN attribution_edits e ON e.id = a.edit_id "
                "WHERE a.memory_id = CAST(:m AS uuid) "
                "ORDER BY a.user_id, COALESCE(e.edit_position, 0) DESC, "
                "         a.created_at DESC"
            ),
            {"m": str(memory_id)},
        )
    ).fetchall()
    return {
        r[0]: {
            "weight": float(r[1]),
            "s1": float(r[2]),
            "s2": float(r[3]),
            "s3": float(r[4]),
            "s4": float(r[5]),
            "s5": float(r[6]),
        }
        for r in rows
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_initial_attribution_persists_computed_signals(
    db_session, test_workspace
):
    """The stored signals come from the scorer, and S4 reflects the content.

    Two documents are created: one full of entities, one with none. Everything
    except structural_score must match, and structural_score must not — which
    is only possible if the values are computed. The old hardcoded tuple gave
    1.0 for both.
    """
    author = await _user(db_session, "Creator")

    rich_mem = await _memory(db_session, test_workspace.id, ENTITY_RICH)
    plain_mem = await _memory(db_session, test_workspace.id, ENTITY_FREE)

    await create_initial_attribution(
        db_session, rich_mem, author, ENTITY_RICH, "text"
    )
    await create_initial_attribution(
        db_session, plain_mem, author, ENTITY_FREE, "text"
    )
    await db_session.flush()

    rich = (await _latest_attributions(db_session, rich_mem))[str(author)]
    plain = (await _latest_attributions(db_session, plain_mem))[str(author)]

    # Constant by construction on a first creation — see the unit tests.
    for row in (rich, plain):
        assert row["weight"] == pytest.approx(1.0)
        assert row["s1"] == pytest.approx(1.0)
        assert row["s2"] == pytest.approx(1.0, abs=1e-4)
        assert row["s3"] == pytest.approx(1.0)
        assert row["s5"] == pytest.approx(0.0)

    # The one value that must differ, and the only one this change corrects.
    assert rich["s4"] == pytest.approx(1.0)
    assert plain["s4"] == pytest.approx(0.0), (
        "entity-free content still scored 1.0 for structural contribution — "
        "the hardcoded value is back"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_contributor_edit_splits_weight_in_the_database(
    db_session, test_workspace
):
    """Two contributors, real split, persisted.

    This is the multi-contributor discrimination the algorithm exists for, and
    the case bulk ingestion never produces: ingestion creates single-author,
    single-edit memories, so nothing ever reaches this path.
    """
    creator = await _user(db_session, "Creator")
    editor = await _user(db_session, "Editor")

    original = "The API rate limit is 100 requests per second."
    revised = (
        "The API rate limit is 500 requests per second for authenticated "
        "clients, enforced by Redis."
    )

    memory_id = await _memory(db_session, test_workspace.id, original)
    await create_initial_attribution(
        db_session, memory_id, creator, original, "text"
    )
    await db_session.flush()

    before = await _latest_attributions(db_session, memory_id)
    assert before[str(creator)]["weight"] == pytest.approx(1.0), (
        "the sole author should hold everything before anyone else edits"
    )

    await recompute_attribution(
        db_session,
        memory_id=memory_id,
        editor_id=editor,
        content_before=original,
        content_after=revised,
        action_type="edit",
    )
    await db_session.flush()

    after = await _latest_attributions(db_session, memory_id)

    assert set(after) == {str(creator), str(editor)}, "both contributors expected"
    weights = {u: v["weight"] for u, v in after.items()}
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)

    for user, weight in weights.items():
        assert 0.0 < weight < 1.0, (
            f"{user} absorbed {weight} of the memory — nothing was split"
        )
    assert weights[str(creator)] != pytest.approx(weights[str(editor)], abs=1e-3), (
        "an even split means the signals carried no information"
    )

    # The split must be driven by the signals, not by an arbitrary constant.
    assert after[str(creator)]["s3"] > after[str(editor)]["s3"], (
        "temporal primacy must favour the first author"
    )
    assert after[str(editor)]["s2"] > after[str(creator)]["s2"], (
        "the editor wrote the surviving text, so more of their phrasing "
        "survives into the final version"
    )

    # Append-only: the creator's original 1.0 row must still exist alongside
    # the recomputed one.
    history = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM attributions "
                "WHERE memory_id = CAST(:m AS uuid) AND user_id = CAST(:u AS uuid)"
            ),
            {"m": str(memory_id), "u": str(creator)},
        )
    ).scalar()
    assert history >= 2, (
        "recompute must append a new row, never overwrite the creator's "
        "original attribution"
    )
