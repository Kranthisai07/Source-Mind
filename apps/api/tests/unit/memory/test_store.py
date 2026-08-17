"""Tests for services/memory/store.py.

GAP 2 — store_memories accepted a source_metadata argument and ignored it, so
every ingested memory was written with tags=NULL and category=NULL. That
silently disabled three downstream features:

  * the importance scorer's category signal always returned its 0.5 default
  * the high_conflict_area gap detector groups by unnest(tags) and could
    never match a row
  * tag filters in search had nothing to match

The parameter was dead at *both* ends — the worker passed a literal {} — so
these tests cover the plumbing as well as the function.
"""

from __future__ import annotations

import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from sourcemind.services.ingestion.embedder import EmbeddingResult

API_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _session_capturing_adds(added: list):
    session = AsyncMock()
    session.add = MagicMock(side_effect=added.append)
    session.flush = AsyncMock()
    return session


def _results(n: int = 2) -> list[EmbeddingResult]:
    return [
        EmbeddingResult(
            content=f"fact number {i}",
            embedding=[0.01 * i] * 3072,
            token_count=0,
            cached=False,
        )
        for i in range(n)
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tags_and_category_are_applied_to_every_memory():
    from sourcemind.services.memory.store import store_memories

    added: list = []
    session = _session_capturing_adds(added)

    memories = await store_memories(
        session,
        uuid.uuid4(),
        uuid.uuid4(),
        _results(3),
        {"tags": ["deployment", "postgres"], "category": "decision"},
    )

    assert len(memories) == 3
    for m in memories:
        assert m.tags == ["deployment", "postgres"], "tags were dropped"
        assert m.category == "decision", "category was dropped"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_absent_metadata_leaves_fields_null_not_empty():
    """No tags must mean NULL, not [] — they are different in SQL.

    `unnest(ARRAY[]::text[])` yields no rows just as NULL does, but an empty
    array still compares unequal to NULL in filters, so keep them distinct.
    """
    from sourcemind.services.memory.store import store_memories

    added: list = []
    memories = await store_memories(
        _session_capturing_adds(added), uuid.uuid4(), uuid.uuid4(), _results(1), {}
    )
    assert memories[0].tags is None
    assert memories[0].category is None


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_metadata",
    [
        {"tags": "not-a-list"},
        {"tags": 42},
        {"category": ["not", "a", "string"]},
    ],
    ids=["tags-string", "tags-int", "category-list"],
)
async def test_malformed_metadata_is_ignored_not_propagated(bad_metadata):
    """Bad shapes come from JSONB and must not reach the INSERT.

    Passing a string where the column expects text[] fails at the driver with
    a confusing error far from the cause.
    """
    from sourcemind.services.memory.store import store_memories

    added: list = []
    memories = await store_memories(
        _session_capturing_adds(added),
        uuid.uuid4(),
        uuid.uuid4(),
        _results(1),
        bad_metadata,
    )
    assert memories[0].tags is None or isinstance(memories[0].tags, list)
    assert memories[0].category is None or isinstance(memories[0].category, str)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_embedding_results_produces_no_memories():
    from sourcemind.services.memory.store import store_memories

    added: list = []
    memories = await store_memories(
        _session_capturing_adds(added), uuid.uuid4(), uuid.uuid4(), [], {"tags": ["x"]}
    )
    assert memories == []


# ─────────────────────────────────────────────────────────────────────────────
# The plumbing. Fixing store_memories alone would have changed nothing.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_worker_passes_real_metadata_not_an_empty_dict():
    """workers/ingestion.py must not call store_memories with a literal {}.

    It did, which made the source_metadata parameter dead at both ends: the
    function ignored it and the caller supplied nothing.
    """
    source = (API_ROOT / "sourcemind" / "workers" / "ingestion.py").read_text(
        encoding="utf-8"
    )
    assert "embedding_results, {})" not in source, (
        "store_memories is still being called with an empty source_metadata"
    )
    assert "source_metadata" in source, "the worker builds no source_metadata"
    assert '.get("tags")' in source, "the worker does not read tags from pipeline_data"


@pytest.mark.unit
def test_receiver_persists_tags_for_the_worker_to_read():
    """receive() must accept tags and store them in pipeline_data.

    This is the hop where tags were actually lost: MemoryCreate accepted them,
    but the route never forwarded them and receive() had no parameter at all.
    """
    source = (
        API_ROOT / "sourcemind" / "services" / "ingestion" / "receiver.py"
    ).read_text(encoding="utf-8")
    assert "tags: list[str] | None = None" in source, "receive() takes no tags"
    assert '"tags": tags or []' in source, "tags are not persisted to pipeline_data"


@pytest.mark.unit
def test_route_forwards_tags_to_the_receiver():
    source = (API_ROOT / "sourcemind" / "api" / "v1" / "memories.py").read_text(
        encoding="utf-8"
    )
    assert "tags=body.tags" in source, (
        "POST /v1/memories accepts tags but does not forward them"
    )
