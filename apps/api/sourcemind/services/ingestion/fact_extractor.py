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
from typing import TYPE_CHECKING, Any, NamedTuple

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

class ChunkExtraction(NamedTuple):
    """One extraction attempt against one chunk.

    `outcome` is the point of this type. Four different conditions used to
    return a bare [] - a genuine empty, a non-list response, unparseable JSON,
    and an API error - and the caller could not tell them apart. A document
    whose extraction collapsed was recorded as a successfully processed empty
    document, indistinguishable from spam.
    """

    facts: list[str]
    outcome: str          # "facts" | "empty" | "failed"
    detail: str = ""
    # Provider usage for this extraction: {"calls", "input_tokens",
    # "output_tokens", "estimated"}; None when no provider call was made.
    usage: dict[str, Any] | None = None


class ExtractionResult(NamedTuple):
    """Everything extract() knows about a document's extraction.

    `facts` alone cannot express "nothing to extract" versus "extraction
    broke", so the failure counts travel with it.
    """

    facts: list[str]
    total_chunks: int
    failed_chunks: int
    failure_reasons: list[str]
    # Aggregated provider usage ({"calls", "input_tokens", "output_tokens",
    # "estimated"}); None when not reported (older callers/stubs).
    usage: dict[str, Any] | None = None

    @property
    def wholly_failed(self) -> bool:
        """No facts AND at least one chunk failed outright."""
        return not self.facts and self.failed_chunks > 0


_MODEL_PRIMARY = "claude-sonnet-4-6"
_DEDUP_THRESHOLD = 0.92  # SBERT cosine similarity above which facts are considered duplicates


def _cache_key(content: str, context: str | None = None) -> str:
    # Context changes what the model sees, so it is part of the key; without
    # context the key is unchanged.
    material = content if not context else f"{content}\0context\0{context}"
    h = hashlib.sha256(material.encode()).hexdigest()
    return f"fact_extract:v1:{h}"


def _new_usage() -> dict[str, Any]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated": False}


def _add_usage(total: dict[str, Any], part: dict[str, Any] | None) -> None:
    if not part:
        return
    total["calls"] += int(part.get("calls", 0))
    total["input_tokens"] += int(part.get("input_tokens", 0))
    total["output_tokens"] += int(part.get("output_tokens", 0))
    total["estimated"] = bool(total["estimated"] or part.get("estimated"))


def _response_usage(response: object, prompt: str, raw: str) -> dict[str, Any]:
    """Usage reported by the provider, else a character-based estimate."""
    usage = getattr(response, "usage", None)
    reported_in = getattr(usage, "input_tokens", None)
    reported_out = getattr(usage, "output_tokens", None)
    if isinstance(reported_in, int) and isinstance(reported_out, int):
        return {
            "calls": 1,
            "input_tokens": reported_in,
            "output_tokens": reported_out,
            "estimated": False,
        }
    return {
        "calls": 1,
        "input_tokens": max(1, len(prompt) // 4),
        "output_tokens": len(raw) // 4,
        "estimated": True,
    }


async def _attempt_extraction(
    client: object,
    chunk_content: str,
    document_date: str | None,
    source_label: str,
    context: str | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> ChunkExtraction:
    """One extraction attempt. Never returns a bare list.

    The API call and the parse are reported separately, following the same
    split established in services/memory/relations.py::_classify_relation: an
    unparseable answer means the model DID respond and the response was thrown
    away, which is a defect, while an empty array is the model correctly
    reporting that there is nothing to extract.
    """
    context_block = ""
    if context:
        # Labels about where the text came from (repo, number, title, url).
        # They are NOT authored text: the model must take facts only from the
        # segment text below, never from these labels.
        context_block = (
            "Context labels (NOT authored text; use only to understand the "
            "segment; do not extract facts from them):\n"
            f"<context>\n{context}\n</context>\n\n"
        )
    content_header = (
        "Segment text (every fact must come from this text):\n" if context else "Content:\n"
    )
    user_prompt = (
        f"Document date: {document_date or 'unknown'}\n"
        f"Source: {source_label}\n\n"
        f"{context_block}"
        f"{content_header}{chunk_content}\n\n"
        "Extract atomic facts:"
    )

    async def _call() -> Any:
        return await client.messages.create(  # type: ignore[union-attr]
            model=_MODEL_PRIMARY,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

    try:
        if semaphore is not None:
            async with semaphore:
                response = await _call()
        else:
            response = await _call()
    except Exception as exc:
        log.error("fact_extract_api_error", error_type=type(exc).__name__)
        return ChunkExtraction(
            [],
            "failed",
            f"api_error:{type(exc).__name__}",
            {"calls": 1, "input_tokens": 0, "output_tokens": 0, "estimated": True},
        )

    raw = response.content[0].text.strip()
    usage = _response_usage(response, user_prompt, raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning(
            "fact_extract_json_error",
            error_type=type(exc).__name__,
            response_length=len(raw),
        )
        return ChunkExtraction([], "failed", "json_error", usage)

    if not isinstance(parsed, list):
        log.warning(
            "fact_extract_invalid_type",
            response_type=type(parsed).__name__,
            response_length=len(raw),
        )
        return ChunkExtraction(
            [], "failed", f"expected a list, got {type(parsed).__name__}", usage
        )

    facts = [f for f in parsed if isinstance(f, str) and f.strip()]

    if not facts:
        # A valid, parsed, empty answer. Not a failure - the model was asked
        # to return [] for content with nothing in it, and did. Logged at info
        # because it is a real outcome worth seeing, not an error.
        log.info("fact_extract_empty", chars=len(chunk_content))
        return ChunkExtraction([], "empty", "model returned an empty array", usage)

    log.debug(
        "fact_extract_done",
        facts=len(facts),
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )
    return ChunkExtraction(facts, "facts", "", usage)


async def _extract_from_chunk(
    client: object,
    chunk_content: str,
    document_date: str | None,
    source_label: str,
    context: str | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> ChunkExtraction:
    """Extract facts from one chunk, retrying once if nothing came back.

    Extraction is not deterministic: re-running it on substantive content that
    had produced nothing has been observed to yield real facts. Accepting the
    first empty answer therefore silently drops content, and because an empty
    result was treated as success there was no signal that it had happened.

    So anything other than facts is attempted a second time. Two empties are
    taken as genuinely empty; two failures as a real failure. Only the cache
    write happens once, at the end, and only for outcomes we trust.
    """
    key = _cache_key(chunk_content, context)
    redis = get_redis()

    cached = await redis.get(key)
    if cached is not None:
        log.debug("fact_extract_cache_hit")
        return ChunkExtraction(json.loads(cached), "facts", "cached")

    first = await _attempt_extraction(
        client, chunk_content, document_date, source_label, context, semaphore
    )
    if first.outcome == "facts":
        await redis.setex(key, 60 * 60 * 24 * 7, json.dumps(first.facts))
        return first

    second = await _attempt_extraction(
        client, chunk_content, document_date, source_label, context, semaphore
    )
    total = _new_usage()
    _add_usage(total, first.usage)
    _add_usage(total, second.usage)
    first = first._replace(usage=total)
    second = second._replace(usage=total)

    if second.outcome == "facts":
        # The first attempt was a transient failure. Worth saying loudly: this
        # is how much of an apparently empty corpus is actually flakiness.
        log.warning(
            "fact_extract_recovered_on_retry",
            first_outcome=first.outcome,
            first_detail=first.detail,
            facts=len(second.facts),
        )
        await redis.setex(key, 60 * 60 * 24 * 7, json.dumps(second.facts))
        return second

    if first.outcome == "empty" and second.outcome == "empty":
        # Two independent, valid empty answers. Genuinely nothing to extract.
        await redis.setex(key, 60 * 60 * 24 * 7, json.dumps([]))
        return ChunkExtraction([], "empty", "empty on both attempts", total)

    # At least one hard failure and no facts either time. Never cached - a
    # failure must not be replayed for seven days as though it were an answer.
    detail = f"attempt1={first.outcome}:{first.detail} attempt2={second.outcome}:{second.detail}"
    log.error("fact_extract_failed_twice", detail=detail)
    return ChunkExtraction([], "failed", detail, total)


# Module-level cache for the SBERT dedup model. Loading it costs 2–5s,
# so we keep one instance for the lifetime of the worker process.
_sbert_model: Any = None


def _get_sbert_model() -> Any:
    """Lazily load and cache the SBERT model used for fact deduplication."""
    global _sbert_model
    if _sbert_model is None:
        from sentence_transformers import SentenceTransformer
        _sbert_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _sbert_model


async def _deduplicate_facts(facts: list[str]) -> list[str]:
    """Remove near-duplicate facts via SBERT cosine similarity."""
    if len(facts) <= 1:
        return facts

    try:
        import numpy as np

        model = _get_sbert_model()
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
        context: str | None = None,
    ) -> ExtractionResult:
        """
        Stage 4 entry point.

        Processes all chunks in parallel and deduplicates. Returns an
        ExtractionResult rather than a bare list so the caller can tell an
        empty document from a broken one; `facts` alone cannot express that
        difference, which is how a failed extraction came to be recorded as a
        successfully processed empty document.

        ``context`` is an optional string of contextual labels (repo, number,
        title, url). It is NOT authored text: it is delimited in the prompt
        and the model is told facts must come from the segment text only.

        Provider calls are bounded by a semaphore of
        ``settings.extraction_concurrency``.
        """
        if not chunks:
            return ExtractionResult([], 0, 0, [])

        source_label = source_url or content_type

        from sourcemind.core.config import get_settings

        semaphore = asyncio.Semaphore(max(1, int(get_settings().extraction_concurrency)))
        tasks = [
            _extract_from_chunk(
                self._client, c.content, document_date, source_label, context, semaphore
            )
            for c in chunks
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_facts: list[str] = []
        failed = 0
        reasons: list[str] = []
        usage = _new_usage()

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                # gather() raised rather than the extractor returning - still a
                # failed chunk, not an empty one.
                failed += 1
                reasons.append(f"chunk {i}: {type(result).__name__}: {result}")
                log.warning(
                    "fact_extract_chunk_skipped", chunk_index=i, error=str(result)
                )
                continue

            _add_usage(usage, result.usage)
            if result.outcome == "failed":
                failed += 1
                reasons.append(f"chunk {i}: {result.detail}")
                continue

            all_facts.extend(result.facts)

        unique_facts = await _deduplicate_facts(all_facts)

        log.info(
            "fact_extraction_complete",
            chunks=len(chunks),
            total_facts=len(all_facts),
            unique_facts=len(unique_facts),
            failed_chunks=failed,
        )
        return ExtractionResult(unique_facts, len(chunks), failed, reasons, usage)
