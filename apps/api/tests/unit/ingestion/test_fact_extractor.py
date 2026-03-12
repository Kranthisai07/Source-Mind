"""Unit tests for Stage 4: FactExtractor."""

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
    chunk = _make_chunk("The project uses PostgreSQL 16 for primary storage.")

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

    assert isinstance(result, list)
    assert len(result) >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_json_parse_failure_skips_chunk_without_crash():
    chunk = _make_chunk("Some content that triggers a bad response.")

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

    assert isinstance(result, list)
    assert len(result) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_chunk_list_returns_empty():
    mock_client = AsyncMock()
    extractor = FactExtractor(mock_client)
    result = await extractor.extract([], content_type="text")
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_hit_skips_api_call():
    chunk = _make_chunk("Cached content.")

    mock_client = AsyncMock()

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(
            return_value=json.dumps(["Cached fact."])
        )
        mock_redis.return_value.setex = AsyncMock()

        extractor = FactExtractor(mock_client)
        result = await extractor.extract([chunk], content_type="text")

    assert "Cached fact." in result
    mock_client.messages.create.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_api_error_on_chunk_doesnt_crash_pipeline():
    chunks = [_make_chunk("Good chunk."), _make_chunk("Chunk that errors.")]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(
        side_effect=[
            _mock_anthropic_response(["A good fact."]),
            RuntimeError("API timeout"),
        ]
    )

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value=None)
        mock_redis.return_value.setex = AsyncMock()

        extractor = FactExtractor(mock_client)
        result = await extractor.extract(chunks, content_type="text")

    assert isinstance(result, list)
