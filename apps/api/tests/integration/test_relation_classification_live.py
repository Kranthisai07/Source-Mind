"""Relation classification against the REAL Anthropic API.

WHY THIS EXISTS, AND WHY IT CANNOT BE MOCKED
--------------------------------------------
Every other test of this code path hands `_classify_relation` a mock client
returning `json.dumps(...)` — clean, unfenced JSON. The real model wrapped its
reply in a markdown fence, `json.loads` failed on the leading backticks, and
the handler converted that into "unrelated" at debug level. The result:
memory_relations and memory_conflicts were BOTH empty in every workspace, for
as long as the code existed, while the full suite passed.

No mocked test could have caught it, because the bug lived entirely in the gap
between what the mocks returned and what the API actually returns. So this file
calls the real thing.

COST: these tests make real, paid Claude Haiku calls — a handful of cents per
run. Skipped automatically when ANTHROPIC_API_KEY is absent, and skippable
explicitly with:

    pytest -m "not costs_api"
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.services.memory.relations import (
    _MIN_CONFIDENCE,
    RelationDetector,
    _classify_relation,
    _extract_json_object,
)


def _api_key() -> str:
    from sourcemind.core.config import get_settings

    return os.environ.get("ANTHROPIC_API_KEY") or get_settings().anthropic_api_key or ""


requires_real_api = pytest.mark.skipif(
    not _api_key(),
    reason="ANTHROPIC_API_KEY not set — this test makes real paid API calls.",
)


def _real_client():
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=_api_key())


# ─── the parse itself ────────────────────────────────────────────────────────

@pytest.mark.integration
@pytest.mark.costs_api
@pytest.mark.slow
@pytest.mark.asyncio
@requires_real_api
async def test_real_model_reply_is_parseable_not_silently_unrelated():
    """A clearly-related pair must not come back as the failure default.

    'unrelated' with confidence 0.0 is exactly what the old code returned for
    EVERY pair, because that is the value the exception handler substitutes.
    Asserting against it is the point: it is the fingerprint of the bug.
    """
    verdict = await _classify_relation(
        _real_client(),
        "We use PostgreSQL with pgvector for semantic search over memories.",
        "The pgvector index is HNSW with m=16 and ef_construction=64.",
    )

    assert verdict.relation != "unrelated", (
        "a clearly-related pair came back 'unrelated' — the classification was "
        "either unparseable or silently discarded"
    )
    assert verdict.confidence > 0.0, (
        "confidence 0.0 is the value the failure handler substitutes"
    )
    assert verdict.confidence >= _MIN_CONFIDENCE, (
        f"confidence {verdict.confidence} is below the {_MIN_CONFIDENCE} "
        "threshold, so no relation would be persisted"
    )
    assert verdict.relation in ("updates", "extends", "derives")


@pytest.mark.integration
@pytest.mark.costs_api
@pytest.mark.slow
@pytest.mark.asyncio
@requires_real_api
async def test_real_model_emits_json_the_extractor_accepts():
    """The system prompt should stop the fencing at source.

    The extractor tolerates a fence anyway — belt and braces, since prompt
    adherence is behaviour rather than a guarantee — but if the model starts
    fencing again this fails loudly instead of degrading to 'unrelated'.
    """
    from sourcemind.services.memory.relations import (
        _CLASSIFY_MAX_TOKENS,
        _CLASSIFY_MODEL,
        _CLASSIFY_SYSTEM_PROMPT,
    )

    response = await _real_client().messages.create(
        model=_CLASSIFY_MODEL,
        max_tokens=_CLASSIFY_MAX_TOKENS,
        system=_CLASSIFY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": 'Reply with JSON: {"ok": true}'}],
    )
    raw = response.content[0].text

    assert not raw.lstrip().startswith("`"), (
        f"model fenced its reply despite the system prompt: {raw[:80]!r}"
    )
    # And whatever it sent must survive the extractor.
    assert _extract_json_object(raw) == {"ok": True}

    # Not truncated: a cut-off reply is invalid JSON and lands in the same
    # silent path the fence did.
    assert response.stop_reason != "max_tokens", (
        f"reply hit the {_CLASSIFY_MAX_TOKENS}-token ceiling"
    )


# ─── the whole chain, real model + real database ─────────────────────────────

async def _user(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:10]
    await session.execute(
        text(
            "INSERT INTO users (id, clerk_id, email, display_name) VALUES "
            "(CAST(:id AS uuid), :clerk, :email, 'Live Test User')"
        ),
        {"id": str(user_id), "clerk": f"clerk-{suffix}", "email": f"{suffix}@t.local"},
    )
    return user_id


async def _memory(
    session: AsyncSession, workspace_id, author_id, content: str, seed: float
) -> uuid.UUID:
    """A memory with a hand-built embedding.

    The vector is fixed rather than produced by OpenAI so the cosine distance
    between the pair is deterministic and sits inside the classification
    radius. What is under test is the LLM step and the parse, not the embedder.
    """
    memory_id = uuid.uuid4()
    vector = "[" + ",".join([f"{seed:.6f}"] * 3072) + "]"
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
            "emb": vector,
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


class _MemoryRow:
    """Stand-in for the ORM objects detect() receives from store_memories."""

    def __init__(self, memory_id, workspace_id, content, embedding):
        self.id = memory_id
        self.workspace_id = workspace_id
        self.content = content
        self.embedding = embedding


@pytest.mark.integration
@pytest.mark.costs_api
@pytest.mark.slow
@pytest.mark.asyncio
@requires_real_api
async def test_detect_writes_a_real_relation_with_the_live_model(
    db_session, test_workspace
):
    """End to end: real model, real database, a row that must exist.

    Before the fence fix this produced zero rows — which is precisely what the
    production database contained: memory_relations was empty, unfiltered.
    """
    author_a = await _user(db_session)
    author_b = await _user(db_session)

    existing = await _memory(
        db_session,
        test_workspace.id,
        author_a,
        "We use PostgreSQL with pgvector for semantic search over memories.",
        seed=0.13,
    )
    incoming = await _memory(
        db_session,
        test_workspace.id,
        author_b,
        "The pgvector index is HNSW, configured with m=16 and ef_construction=64.",
        seed=0.13,
    )

    await RelationDetector(_real_client()).detect(
        db_session,
        [
            _MemoryRow(
                incoming,
                test_workspace.id,
                "The pgvector index is HNSW, configured with m=16 and "
                "ef_construction=64.",
                [0.13] * 3072,
            )
        ],
        test_workspace.id,
    )

    rows = (
        await db_session.execute(
            text(
                "SELECT relation_type, confidence FROM memory_relations "
                "WHERE source_memory_id = CAST(:s AS uuid) "
                "  AND target_memory_id = CAST(:t AS uuid)"
            ),
            {"s": str(incoming), "t": str(existing)},
        )
    ).fetchall()

    assert rows, (
        "no MemoryRelation was written for a clearly-related pair through the "
        "real classification path"
    )
    relation_type, confidence = rows[0]
    assert relation_type in ("updates", "extends", "derives")
    assert confidence >= _MIN_CONFIDENCE

    # Importance reads inbound edges, so the target's score must now move off
    # the column default it was stuck at while this path wrote nothing.
    inbound = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM memory_relations "
                "WHERE target_memory_id = CAST(:t AS uuid)"
            ),
            {"t": str(existing)},
        )
    ).scalar()
    assert inbound >= 1, "the relation must be visible to the importance query"
