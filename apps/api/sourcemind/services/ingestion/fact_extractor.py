"""
Stage 4 — FACT EXTRACT: Extract atomic, self-contained facts from chunks.

Uses Claude Sonnet for high-quality extraction.
Results are cached in Redis (TTL: 7 days) by chunk content SHA-256.
Chunks are processed in parallel via asyncio.gather().
Near-duplicate facts are removed via SBERT cosine similarity (threshold 0.92).

Usage:
  extractor = FactExtractor(anthropic_client)
  facts = await extractor.extract(chunks, content_type="text")
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

import structlog

from sourcemind.core.redis_client import get_redis

if TYPE_CHECKING:
    from sourcemind.services.ingestion.chunker import ChunkResult

log = structlog.get_logger(__name__)

# Exactly as specified — do not change without updating ADR-004
_SYSTEM_PROMPT = """\
You are a knowledge extraction specialist for an enterprise memory platform. \
Extract atomic, self-contained facts from text.

RULES — follow all of them without exception:
1. Each fact must be completely understandable WITHOUT any surrounding context
2. Resolve ALL pronouns: never write "they", "it", "this", "the team" — \
always use the specific name/entity
3. Ground ALL relative time: "last week" → infer actual date if document \
date is provided, otherwise write "as of [document date]"
4. One claim per fact — never combine two ideas with "and" or "also"
5. Exclude: opinions without evidence, filler sentences, procedural steps \
("click here", "see section 3"), metadata about the document itself
6. For code chunks: extract design decisions, constraints, and documented \
behaviors — not descriptions of what the code literally does line-by-line
7. Facts must be specific — "The system uses OAuth 2.0" not "The system \
uses an authentication method"

Return ONLY a JSON array of strings.
No preamble. No explanation. No markdown. No code fences.
Minimum 1 fact. Maximum 10 facts. Target 3–7.
If the content contains no extractable facts, return: []\
"""

_MODEL_PRIMARY = "claude-sonnet-4-6"
_DEDUP_THRESHOLD = 0.92  # SBERT cosine similarity above which facts are considered duplicates


def _cache_key(content: str) -> str:
    h = hashlib.sha256(content.encode()).hexdigest()
    return f"fact_extract:v1:{h}"


async def _extract_from_chunk(
    client: object,
    chunk_content: str,
    document_date: str | None,
    source_label: str,
) -> list[str]:
    """Extract facts from a single chunk. Uses Redis cache."""
    key = _cache_key(chunk_content)
    redis = get_redis()

    cached = await redis.get(key)
    if cached:
        log.debug("fact_extract_cache_hit")
        return json.loads(cached)

    user_prompt = (
        f"Document date: {document_date or 'unknown'}\n"
        f"Source: {source_label}\n\n"
        f"Content:\n{chunk_content}\n\n"
        "Extract atomic facts:"
    )

    try:
        response = await client.messages.create(  # type: ignore[union-attr]
            model=_MODEL_PRIMARY,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()
        facts: list[str] = json.loads(raw)

        if not isinstance(facts, list):
            log.warning("fact_extract_invalid_type", raw_preview=raw[:200])
            return []

        facts = [f for f in facts if isinstance(f, str) and f.strip()]

        # Cache for 7 days
        await redis.setex(key, 60 * 60 * 24 * 7, json.dumps(facts))

        log.debug(
            "fact_extract_done",
            facts=len(facts),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        return facts

    except json.JSONDecodeError as exc:
        log.warning("fact_extract_json_error", error=str(exc))
        return []
    except Exception as exc:
        log.error("fact_extract_api_error", error=str(exc))
        return []


async def _deduplicate_facts(facts: list[str]) -> list[str]:
    """
    Remove near-duplicate facts using SBERT cosine similarity.
    Uses all-MiniLM-L6-v2 — fast, small, good for sentence-level similarity.
    """
    if len(facts) <= 1:
        return facts

    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        embeddings = model.encode(facts, normalize_embeddings=True, show_progress_bar=False)

        unique_facts: list[str] = [facts[0]]
        unique_embs: list[Any] = [embeddings[0]]

        for i in range(1, len(facts)):
            sims = [float(np.dot(embeddings[i], ue)) for ue in unique_embs]
            if max(sims) < _DEDUP_THRESHOLD:
                unique_facts.append(facts[i])
                unique_embs.append(embeddings[i])

        removed = len(facts) - len(unique_facts)
        if removed:
            log.debug("fact_dedup", before=len(facts), after=len(unique_facts), removed=removed)
        return unique_facts

    except Exception as exc:
        log.warning("fact_dedup_failed", error=str(exc))
        return facts  # Return all on error


class FactExtractor:
    """
    Extracts atomic facts from text chunks using Claude.

    The Anthropic client is injected at construction — never instantiated internally.
    This makes testing easy: pass a mock client directly.
    """

    def __init__(self, client: object) -> None:
        self._client = client

    async def extract(
        self,
        chunks: list[ChunkResult],
        document_date: str | None = None,
        source_url: str | None = None,
        content_type: str = "text",
    ) -> list[str]:
        """
        Stage 4 entry point.

        Processes all chunks in parallel, deduplicates results.
        Skips failed chunks without crashing the pipeline.
        """
        if not chunks:
            return []

        source_label = source_url or content_type

        tasks = [
            _extract_from_chunk(self._client, c.content, document_date, source_label)
            for c in chunks
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_facts: list[str] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                log.warning("fact_extract_chunk_skipped", chunk_index=i, error=str(result))
            else:
                all_facts.extend(result)  # type: ignore[arg-type]

        unique_facts = await _deduplicate_facts(all_facts)

        log.info(
            "fact_extraction_complete",
            chunks=len(chunks),
            total_facts=len(all_facts),
            unique_facts=len(unique_facts),
        )
        return unique_facts
