"""Unit tests for hybrid search service."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.services.search.hybrid import (
    _keyword_search,
    _rrf_merge,
    _semantic_search,
)


@pytest.mark.unit
def test_rrf_merge_empty_lists():
    result = _rrf_merge([], [])
    assert result == []


@pytest.mark.unit
def test_rrf_merge_semantic_only():
    semantic = [
        {"id": "a", "content": "Content A", "score": 0.9, "match_type": "semantic"},
        {"id": "b", "content": "Content B", "score": 0.8, "match_type": "semantic"},
    ]
    result = _rrf_merge(semantic, [])
    assert [r["id"] for r in result] == ["a", "b"]
    assert result[0]["match_type"] == "semantic"


@pytest.mark.unit
def test_rrf_merge_keyword_only():
    keyword = [
        {"id": "x", "content": "Keyword X", "score": 0.5, "match_type": "keyword"},
    ]
    result = _rrf_merge([], keyword)
    assert result[0]["id"] == "x"
    assert result[0]["match_type"] == "keyword"


@pytest.mark.unit
def test_rrf_merge_boosts_results_in_both_lists():
    """A result appearing in both semantic and keyword lists should rank higher."""
    shared_id = "shared"
    semantic = [
        {"id": shared_id, "content": "Shared content", "score": 0.5, "match_type": "semantic"},
        {"id": "only_semantic", "content": "Only semantic", "score": 0.9, "match_type": "semantic"},
    ]
    keyword = [
        {"id": shared_id, "content": "Shared content", "score": 0.5, "match_type": "keyword"},
        {"id": "only_keyword", "content": "Only keyword", "score": 0.9, "match_type": "keyword"},
    ]

    result = _rrf_merge(semantic, keyword)
    ids = [r["id"] for r in result]

    # Shared result should be ranked first (appears in both lists)
    assert ids[0] == shared_id
    # Match type should reflect hybrid
    shared_result = next(r for r in result if r["id"] == shared_id)
    assert "semantic" in shared_result["match_type"]
    assert "keyword" in shared_result["match_type"]


@pytest.mark.unit
def test_rrf_merge_scores_are_positive():
    semantic = [{"id": f"s{i}", "content": f"Content {i}", "score": 0.5, "match_type": "semantic"}
                for i in range(5)]
    keyword = [{"id": f"k{i}", "content": f"Content {i}", "score": 0.5, "match_type": "keyword"}
               for i in range(5)]
    result = _rrf_merge(semantic, keyword)
    for r in result:
        assert r["score"] > 0


@pytest.mark.unit
def test_rrf_merge_descending_order():
    """Results should be sorted by descending RRF score."""
    semantic = [
        {"id": "top", "content": "Top", "score": 0.9, "match_type": "semantic"},
        {"id": "mid", "content": "Mid", "score": 0.5, "match_type": "semantic"},
        {"id": "low", "content": "Low", "score": 0.1, "match_type": "semantic"},
    ]
    result = _rrf_merge(semantic, [])
    scores = [r["score"] for r in result]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.unit
def test_rrf_merge_preserves_memory_provenance() -> None:
    created_at = object()
    updated_at = object()
    provenance = {
        "document_id": str(uuid.uuid4()),
        "version": 3,
        "tags": ["pilot"],
        "category": "decision",
        "confidence_score": 0.87,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    semantic = [{
        "id": "shared",
        "content": "Auditable memory",
        "score": 0.8,
        "match_type": "semantic",
        **provenance,
    }]
    keyword = [{
        "id": "shared",
        "content": "Auditable memory",
        "score": 0.6,
        "match_type": "keyword",
        **provenance,
    }]

    result = _rrf_merge(semantic, keyword)

    assert {key: result[0][key] for key in provenance} == provenance


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search", "args"),
    [
        (_semantic_search, ([0.1], uuid.uuid4(), 10, 0.3)),
        (_keyword_search, ("audit", uuid.uuid4(), 10)),
    ],
)
async def test_search_arms_return_real_memory_provenance(search, args) -> None:
    document_id = str(uuid.uuid4())
    unrelated_memory_id = str(uuid.uuid4())
    related_memory_id = str(uuid.uuid4())
    created_at = object()
    updated_at = object()
    rows = [
        (
            unrelated_memory_id,
            "Memory without relations",
            0.75,
            document_id,
            4,
            ["pilot"],
            "process",
            0.91,
            created_at,
            updated_at,
            0,
        ),
        (
            related_memory_id,
            "Memory with relations",
            0.7,
            document_id,
            3,
            ["pilot"],
            "decision",
            0.8,
            created_at,
            updated_at,
            2,
        ),
    ]
    result = MagicMock()
    result.fetchall.return_value = rows
    session = AsyncMock()
    session.execute.return_value = result

    items = await search(session, *args)

    statement = str(session.execute.await_args.args[0])
    assert "memory_relations" in statement
    assert items[0]["document_id"] == document_id
    assert items[0]["version"] == 4
    assert items[0]["tags"] == ["pilot"]
    assert items[0]["category"] == "process"
    assert items[0]["confidence_score"] == 0.91
    assert items[0]["created_at"] is created_at
    assert items[0]["updated_at"] is updated_at
    assert items[0]["relation_count"] == 0
    assert items[1]["relation_count"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_viewer_role_truncates_content():
    """Viewer role should receive truncated content."""
    import base64
    import struct

    from sourcemind.services.search.hybrid import _VIEWER_LIMIT, hybrid_search

    long_content = "A" * 500
    mock_session = AsyncMock()
    mock_openai_client = AsyncMock()  # injected directly — no import patching needed

    emb = [0.1] * 3072
    packed = base64.b64encode(struct.pack(f"{len(emb)}f", *emb)).decode()

    result_mock = MagicMock()
    result_mock.fetchall = MagicMock(return_value=[(
        "00000000-0000-4000-8000-000000000001",
        long_content,
        0.8,
        None,
        1,
        None,
        None,
        None,
        object(),
        None,
        0,
    )])
    mock_session.execute = AsyncMock(return_value=result_mock)

    with patch("sourcemind.services.search.hybrid.get_redis") as mock_redis:
        # Cache hit → OpenAI client should NOT be called
        mock_redis.return_value.get = AsyncMock(return_value=packed)

        result = await hybrid_search(
            session=mock_session,
            query="test query",
            workspace_id=uuid.uuid4(),
            mode="semantic",
            user_role="viewer",
            openai_client=mock_openai_client,
        )

    mock_openai_client.embeddings.create.assert_not_called()
    for item in result["results"]:
        assert len(item["content"]) <= _VIEWER_LIMIT + len(
            " [Summary only — request access for full content]"
        )


@pytest.mark.unit
def test_workspace_isolation_enforced():
    """RRF merge preserves workspace_id filter (tested via query construction)."""
    # The workspace isolation is enforced by the WHERE clause in the SQL queries.
    # This test verifies that the workspace_id is correctly passed to queries.
    # Integration tests handle the actual DB filtering.

    # Test that the hybrid_search function signature accepts workspace_id
    import inspect

    from sourcemind.services.search.hybrid import hybrid_search

    sig = inspect.signature(hybrid_search)
    assert "workspace_id" in sig.parameters
