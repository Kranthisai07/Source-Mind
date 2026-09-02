"""Unit tests for Stage 4: FactExtractor.

extract() returns an ExtractionResult rather than a bare list, because a list
cannot express the difference between "nothing to extract" and "extraction
broke" - and every one of those four conditions used to return [].

Anything other than facts is now attempted twice, so mocks that previously
supplied one response supply two.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.services.ingestion.chunker import ChunkResult
from sourcemind.services.ingestion.fact_extractor import FactExtractor


def _make_chunk(content: str, idx: int = 0) -> ChunkResult:
    return ChunkResult(
        content=content,
        token_count=len(content.split()),
        chunk_index=idx,
        total_chunks=1,
        chunk_type="text",
        metadata={},
    )


def _mock_anthropic_response(facts: list[str]) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(facts))]
    response.usage = MagicMock(input_tokens=100, output_tokens=50)
    return response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_facts_returns_list():
    chunk = _make_chunk(
        "The project uses PostgreSQL 16 for primary storage. "
        "Redis 7.2 backs the cache and the Celery broker."
    )

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(
        return_value=_mock_anthropic_response(
            ["The project uses PostgreSQL 16 for primary storage."]
        )
    )

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value=None)
        mock_redis.return_value.setex = AsyncMock()

        extractor = FactExtractor(mock_client)
        result = await extractor.extract([chunk], content_type="text")

    assert len(result.facts) >= 1
    assert result.failed_chunks == 0
    # One attempt is enough when the first one produces facts.
    assert mock_client.messages.create.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_json_parse_failure_skips_chunk_without_crash():
    chunk = _make_chunk(
        "Some content that triggers a bad response. It spans two sentences so "
        "that it reaches the extraction call rather than the atomic-content "
        "shortcut."
    )

    bad_response = MagicMock()
    bad_response.content = [MagicMock(text="NOT VALID JSON AT ALL")]
    bad_response.usage = MagicMock(input_tokens=10, output_tokens=5)

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=bad_response)

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value=None)
        mock_redis.return_value.setex = AsyncMock()

        extractor = FactExtractor(mock_client)
        result = await extractor.extract([chunk], content_type="text")

    assert result.facts == []
    # Unparseable is a FAILURE, not an empty document.
    assert result.failed_chunks == 1
    assert result.wholly_failed is True
    assert "json_error" in result.failure_reasons[0]
    # Retried once before giving up.
    assert mock_client.messages.create.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_chunk_list_returns_empty():
    mock_client = AsyncMock()
    extractor = FactExtractor(mock_client)
    result = await extractor.extract([], content_type="text")
    assert result.facts == []
    assert result.total_chunks == 0
    # No chunks is not a failure.
    assert result.failed_chunks == 0
    assert result.wholly_failed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_hit_skips_api_call():
    chunk = _make_chunk(
        "Cached content. It is deliberately multi-sentence so the cache lookup "
        "is reached at all."
    )

    mock_client = AsyncMock()

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(
            return_value=json.dumps(["Cached fact."])
        )
        mock_redis.return_value.setex = AsyncMock()

        extractor = FactExtractor(mock_client)
        result = await extractor.extract([chunk], content_type="text")

    assert "Cached fact." in result.facts
    assert result.failed_chunks == 0
    mock_client.messages.create.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_api_error_on_chunk_doesnt_crash_pipeline():
    chunks = [
        _make_chunk("Good chunk. It has two sentences so extraction runs."),
        _make_chunk("Chunk that errors. It also has two sentences."),
    ]

    # Chunks run concurrently, so responses cannot be matched to chunks by
    # position. Dispatch on the prompt instead: the erroring chunk fails both
    # of its attempts, the good one succeeds on its first.
    async def respond(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        if "Chunk that errors." in prompt:
            raise RuntimeError("API timeout")
        return _mock_anthropic_response(["A good fact."])

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(side_effect=respond)

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value=None)
        mock_redis.return_value.setex = AsyncMock()

        extractor = FactExtractor(mock_client)
        result = await extractor.extract(chunks, content_type="text")

    # The good chunk still contributes; the broken one is counted, not hidden.
    assert result.facts == ["A good fact."]
    assert result.failed_chunks == 1
    assert result.total_chunks == 2
    # Facts survived, so this is partial rather than wholly failed.
    assert result.wholly_failed is False
