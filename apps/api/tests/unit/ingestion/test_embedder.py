"""Unit tests for Stage 5: EmbeddingService."""

import base64
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.services.ingestion.embedder import (
    EmbeddingResult,
    EmbeddingService,
    _cache_key,
    _pack,
    _unpack,
)


def _fake_embedding(n: int = 3072) -> list[float]:
    return [float(i) / n for i in range(n)]


def _fake_openai_response(embeddings: list[list[float]]) -> MagicMock:
    response = MagicMock()
    response.data = [MagicMock(embedding=emb) for emb in embeddings]
    return response


@pytest.mark.unit
def test_pack_unpack_roundtrip():
    emb = _fake_embedding()
    packed = _pack(emb)
    assert isinstance(packed, str)

    unpacked = _unpack(packed)
    assert len(unpacked) == 3072
    # Float32 precision loss is expected, check approximate equality
    for a, b in zip(emb[:10], unpacked[:10]):
        assert abs(a - b) < 1e-5


@pytest.mark.unit
def test_cache_key_deterministic():
    text = "hello world"
    key1 = _cache_key(text)
    key2 = _cache_key(text)
    assert key1 == key2
    assert key1.startswith("emb:v1:")


@pytest.mark.unit
def test_cache_key_differs_for_different_inputs():
    assert _cache_key("foo") != _cache_key("bar")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_hit_skips_api_call():
    facts = ["The system uses PostgreSQL 16."]
    emb = _fake_embedding()
    packed = _pack(emb)

    mock_client = AsyncMock()

    with patch("sourcemind.services.ingestion.embedder.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value=packed)
        mock_redis.return_value.setex = AsyncMock()

        service = EmbeddingService(mock_client)
        results = await service.embed(facts)

    assert len(results) == 1
    assert results[0].cached is True
    mock_client.embeddings.create.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_miss_calls_api():
    facts = ["New fact not in cache."]
    emb = _fake_embedding()

    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(
        return_value=_fake_openai_response([emb])
    )

    with patch("sourcemind.services.ingestion.embedder.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value=None)
        mock_redis.return_value.setex = AsyncMock()

        service = EmbeddingService(mock_client)
        results = await service.embed(facts)

    assert len(results) == 1
    assert results[0].cached is False
    assert len(results[0].embedding) == 3072
    mock_redis.return_value.setex.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_facts_returns_empty():
    mock_client = AsyncMock()
    service = EmbeddingService(mock_client)
    result = await service.embed([])
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multiple_facts_batched():
    facts = [f"Fact number {i}." for i in range(5)]
    embeddings = [_fake_embedding() for _ in facts]

    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(
        return_value=_fake_openai_response(embeddings)
    )

    with patch("sourcemind.services.ingestion.embedder.get_redis") as mock_redis:
        mock_redis.return_value.get = AsyncMock(return_value=None)
        mock_redis.return_value.setex = AsyncMock()

        service = EmbeddingService(mock_client)
        results = await service.embed(facts)

    assert len(results) == 5
    assert mock_redis.return_value.setex.call_count == 5
