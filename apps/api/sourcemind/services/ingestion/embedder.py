"""
Stage 5 — EMBED: Generate vector(3072) embeddings for memory strings.

Model: OpenAI text-embedding-3-large (dimensions=3072)

Caching strategy:
  Key: emb:v1:{sha256(content)}
  Value: base64-encoded float32 binary (stored as string due to decode_responses=True)
  TTL: 30 days
  Savings: ~10x vs JSON on storage, eliminates redundant API calls

Batching: up to 2048 inputs per API call.
Retry: exponential backoff on 429 (1s, 2s, 4s, max 3 attempts).

Usage:
  service = EmbeddingService(openai_client)
  results = await service.embed(["fact 1", "fact 2"])
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from dataclasses import dataclass

import structlog

from sourcemind.core.redis_client import get_redis

log = structlog.get_logger(__name__)

_MODEL = "text-embedding-3-large"
_DIMENSIONS = 3072
_BATCH_SIZE = 2048
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days


@dataclass
class EmbeddingResult:
    """A single fact + its embedding vector, with a cache-hit indicator."""

    content: str
    embedding: list[float]
    token_count: int
    cached: bool


def _cache_key(content: str) -> str:
    h = hashlib.sha256(content.encode()).hexdigest()
    return f"emb:v1:{h}"


def _pack(embedding: list[float]) -> str:
    """Pack float32 list → base64 string (Redis stores strings with decode_responses=True)."""
    raw = struct.pack(f"{len(embedding)}f", *embedding)
    return base64.b64encode(raw).decode("ascii")


def _unpack(data: str) -> list[float]:
    """Unpack base64 string → float32 list."""
    raw = base64.b64decode(data.encode("ascii"))
    count = len(raw) // 4
    return list(struct.unpack(f"{count}f", raw))


async def _embed_batch(client: object, texts: list[str], retries: int = 3) -> list[list[float]]:
    """Embed a batch with exponential backoff on rate limits."""
    for attempt in range(retries):
        try:
            response = await client.embeddings.create(  # type: ignore[union-attr]
                model=_MODEL,
                input=texts,
                dimensions=_DIMENSIONS,
            )
            return [item.embedding for item in response.data]
        except Exception as exc:
            if "429" in str(exc) and attempt < retries - 1:
                wait = 2**attempt
                log.warning("embed_rate_limit", attempt=attempt, wait_s=wait)
                await asyncio.sleep(wait)
            else:
                raise

    raise RuntimeError("unreachable: retry loop must either return or raise")


class EmbeddingService:
    """
    Embeds text strings using OpenAI's text-embedding-3-large model.
    Caches embeddings in Redis to avoid redundant API calls.

    The OpenAI client is injected at construction — never instantiated internally.
    This makes testing easy: pass a mock client directly.
    """

    def __init__(self, client: object) -> None:
        self._client = client

    async def embed(self, facts: list[str]) -> list[EmbeddingResult]:
        """
        Stage 5 entry point.

        Checks Redis cache for each fact, batches uncached facts for OpenAI,
        stores results back in cache.
        """
        if not facts:
            return []

        redis = get_redis()

        results: list[EmbeddingResult | None] = [None] * len(facts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # Cache check
        for i, fact in enumerate(facts):
            cached_val = await redis.get(_cache_key(fact))
            if cached_val:
                results[i] = EmbeddingResult(
                    content=fact,
                    embedding=_unpack(cached_val),
                    token_count=0,
                    cached=True,
                )
            else:
                uncached_indices.append(i)
                uncached_texts.append(fact)

        # Batch-embed uncached facts
        if uncached_texts:
            all_embeddings: list[list[float]] = []
            for batch_start in range(0, len(uncached_texts), _BATCH_SIZE):
                batch = uncached_texts[batch_start: batch_start + _BATCH_SIZE]
                batch_embeddings = await _embed_batch(self._client, batch)
                all_embeddings.extend(batch_embeddings)

            for fact_idx, text, embedding in zip(
                uncached_indices, uncached_texts, all_embeddings, strict=True
            ):
                key = _cache_key(text)
                await redis.setex(key, _CACHE_TTL, _pack(embedding))
                results[fact_idx] = EmbeddingResult(
                    content=text,
                    embedding=embedding,
                    token_count=0,
                    cached=False,
                )

        valid = [r for r in results if r is not None]
        cache_hits = sum(1 for r in valid if r.cached)

        log.info(
            "embed_complete",
            total=len(facts),
            cache_hits=cache_hits,
            api_calls=len(uncached_texts),
            cache_hit_rate=f"{cache_hits / max(len(facts), 1):.1%}",
        )
        return valid
