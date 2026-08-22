"""Writing a relation must rescore the TARGET, not only the source.

importance_score's first signal counts inbound edges:

    SELECT COUNT(*) FROM memory_relations WHERE target_memory_id = :mid

So writing an edge changes the score of the memory the edge POINTS AT, and
leaves the source's inbound count untouched. detect()'s post-processing
iterates new_memories — every one of which is a source — so the memory whose
score actually moved was never recomputed.

The visible result was a workspace where importance_score had exactly one
distinct value across 234 memories, including three that had inbound edges.
Recomputing one of those by hand moved it 0.1700 -> 0.2556, which is how the
gap was found: the relation was right, the formula was right, nothing
connected them.

Identical in shape to the Phase 2 conflict-cluster fix, where
_maybe_create_conflict had to call recompute_severity_for_memory(cand_id)
because iterating one side of a relationship skips the other.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.services.memory.relations import RelationDetector


def _claude_saying_extends():
    """A confident, non-conflicting 'extends' verdict.

    'extends' on purpose rather than 'updates': an updates verdict retires the
    target (current_version = FALSE), and a retired memory would muddy an
    assertion about that same memory's score.
    """
    client = MagicMock()
    payload = json.dumps(
        {
            "relation": "extends",
            "confidence": 0.95,
            "is_conflict": False,
            "conflict_summary": "",
        }
    )
    client.messages = MagicMock()
    client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=payload)])
    )
    return client


def _vector(seed: float) -> str:
    return "[" + ",".join([f"{seed:.6f}"] * 3072) + "]"


def _orthogonal_vector() -> str:
    """A vector genuinely far from the constant ones, in COSINE terms.

    Constant vectors all point the same direction, so [0.21]*n and [0.99]*n
    are cosine distance 0.0 apart no matter how different the magnitudes look.
    Alternating signs gives distance 1.0, which is what "unrelated" needs to
    mean here.
    """
    return "[" + ",".join("0.5" if i % 2 == 0 else "-0.5" for i in range(3072)) + "]"


async def _user(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:10]
    await session.execute(
        text(
            "INSERT INTO users (id, clerk_id, email, display_name) VALUES "
            "(CAST(:id AS uuid), :clerk, :email, 'Rescore Test User')"
        ),
        {"id": str(user_id), "clerk": f"clerk-{suffix}", "email": f"{suffix}@t.local"},
    )
    return user_id


async def _memory(
    session: AsyncSession,
    workspace_id,
    author_id,
    *,
    seed: float,
    content: str,
    embedding: str | None = None,
) -> uuid.UUID:
    memory_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO memories (id, workspace_id, content, content_hash, version, "
            "current_version, importance_score, embedding) VALUES "
            "(CAST(:id AS uuid), CAST(:ws AS uuid), :content, :hash, 1, TRUE, 0.5, "
            "CAST(:emb AS vector))"
        ),
        {
            "id": str(memory_id),
            "ws": str(workspace_id),
            "content": content,
            "hash": uuid.uuid4().hex,
            "emb": embedding or _vector(seed),
        },
    )
    await session.execute(
        text(
            "INSERT INTO attributions (memory_id, user_id, contribution_weight, "
            "trigger_action) VALUES (CAST(:m AS uuid), CAST(:u AS uuid), 1.0, 'create')"
        ),
        {"m": str(memory_id), "u": str(author_id)},
    )
    await session.flush()
    return memory_id


async def _importance(session: AsyncSession, memory_id) -> float:
    return float(
        (
            await session.execute(
                text(
                    "SELECT importance_score FROM memories "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(memory_id)},
            )
        ).scalar()
    )


class _MemoryRow:
    """What store_memories hands detect() on the real ingestion path."""

    def __init__(self, memory_id, workspace_id, content, embedding):
        self.id = memory_id
        self.workspace_id = workspace_id
        self.content = content
        self.embedding = embedding


@pytest.mark.integration
@pytest.mark.asyncio
async def test_relation_target_importance_is_recomputed_not_just_the_source(
    db_session, test_workspace
):
    """Both ends of the edge must end up scored, and the TARGET specifically.

    A control memory with no inbound edge is created alongside, so the test
    distinguishes "the target was rescored because of its inbound edge" from
    "detect() happened to rescore everything in the workspace".
    """
    author_a = await _user(db_session)
    author_b = await _user(db_session)

    target = await _memory(
        db_session,
        test_workspace.id,
        author_a,
        seed=0.21,
        content="We store memories in PostgreSQL with pgvector.",
    )
    # Genuinely far in cosine terms, so it is never a candidate.
    control = await _memory(
        db_session,
        test_workspace.id,
        author_a,
        seed=0.0,
        content="Unrelated note about office snacks.",
        embedding=_orthogonal_vector(),
    )
    incoming_content = "The pgvector index uses HNSW with m=16."
    incoming = await _memory(
        db_session, test_workspace.id, author_b, seed=0.21, content=incoming_content
    )

    # Establish real baselines. The 0.5 seeded above is the column default -
    # precisely the stale value this whole investigation started from - so
    # comparing against it would compare against nothing meaningful.
    from sourcemind.services.memory.importance import recompute_importance

    await recompute_importance(db_session, target)
    await recompute_importance(db_session, control)
    target_before = await _importance(db_session, target)
    control_before = await _importance(db_session, control)

    await RelationDetector(_claude_saying_extends()).detect(
        db_session,
        [_MemoryRow(incoming, test_workspace.id, incoming_content, [0.21] * 3072)],
        test_workspace.id,
    )

    # The edge must exist, pointing at `target`.
    edge = (
        await db_session.execute(
            text(
                "SELECT relation_type FROM memory_relations "
                "WHERE source_memory_id = CAST(:s AS uuid) "
                "  AND target_memory_id = CAST(:t AS uuid)"
            ),
            {"s": str(incoming), "t": str(target)},
        )
    ).first()
    assert edge is not None, "no relation was written, so there is nothing to rescore"

    target_after = await _importance(db_session, target)
    control_after = await _importance(db_session, control)
    source_after = await _importance(db_session, incoming)

    # THE POINT OF THIS TEST: the target, addressed by target_memory_id.
    assert target_after != target_before, (
        f"target {target} still scores {target_after} — it gained an inbound "
        "relation and was never recomputed"
    )
    assert target_after > target_before, (
        "an inbound relation should raise importance, not lower it"
    )

    # The source is rescored too, by the pre-existing new_memories loop.
    assert source_after != 0.5, "the source memory was not recomputed either"

    # And the control proves the change is attributable to the inbound edge
    # rather than a blanket rescore of the workspace.
    assert control_after == control_before, (
        "a memory with no inbound relation should not have moved"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_target_rescore_matches_a_direct_recompute(db_session, test_workspace):
    """detect() must leave the target at the value recompute would produce.

    Guards against the target being rescored at the wrong moment — before the
    edge is flushed, say — which would persist a score computed from an inbound
    count of zero and look identical to no rescore at all.
    """
    from sourcemind.services.memory.importance import recompute_importance

    author_a = await _user(db_session)
    author_b = await _user(db_session)

    target = await _memory(
        db_session, test_workspace.id, author_a, seed=0.31,
        content="Search combines pgvector similarity with BM25 ranking.",
    )
    incoming_content = "The two result sets are fused with Reciprocal Rank Fusion."
    incoming = await _memory(
        db_session, test_workspace.id, author_b, seed=0.31, content=incoming_content
    )

    await RelationDetector(_claude_saying_extends()).detect(
        db_session,
        [_MemoryRow(incoming, test_workspace.id, incoming_content, [0.31] * 3072)],
        test_workspace.id,
    )

    persisted = await _importance(db_session, target)
    recomputed = await recompute_importance(db_session, target)

    assert persisted == pytest.approx(recomputed, abs=1e-6), (
        f"detect() left the target at {persisted}, but recomputing it now "
        f"gives {recomputed} — the rescore ran against a stale inbound count"
    )
