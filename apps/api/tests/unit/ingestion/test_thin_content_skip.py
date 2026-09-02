"""Content that is already one atomic fact should not be re-stated by Claude.

MEASURED PROBLEM
----------------
A one-line commit title extracts into two or three facts that each restate the
full context, because the prompt requires every fact to stand alone with
pronouns resolved. What comes out is boilerplate differing only in an
identifier:

    "The GitHub repository 'facebook/react' contains commit 561ed529b3a6 ..."
    "The GitHub repository facebook/react received commit 75ae73e68c02 ..."

Those two sit 0.081 apart in cosine distance, measured on the stored
embeddings - well inside the 0.15 conflict threshold. The identifier
contributes almost nothing to the vector, so each of those facts answers every
similarly-shaped query about any react commit equally well. In the 300-item
evaluation the same five such commits occupied the top-5 for query after query.

Embedding the original line once keeps the distinguishing text at full weight
rather than diluting it across restatements.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.services.ingestion.chunker import ChunkResult
from sourcemind.services.ingestion.fact_extractor import (
    _THIN_CONTENT_CHARS,
    FactExtractor,
)

# Verbatim from the evaluation corpus - these four dominated every react query.
ATTRACTOR = (
    "[facebook/react] Commit 561ed529b3a6 by eps1lon: Fix formatting (#36332)"
)
LONG_SOURCE = (
    "[facebook/react] Commit 142cfde89eda by Dhakshin2007: Fix FragmentInstance "
    "listener leak: normalize boolean vs object capture options per DOM spec "
    "(#36047)\n\n## Summary\n\n`FragmentInstance.addEventListener` and "
    "`removeEventListener` fail to cross-match listeners when the `capture` "
    "option is passed as a boolean in one call and an options object in the "
    "other. This violates the DOM Living Standard, which specifies that "
    "addEventListener(type, fn, true) and addEventListener(type, fn, "
    "{capture: true}) are identical. The result is a listener leak."
)
SHORT_MULTI = (
    "The maximum upload size is 50 megabytes. Requests above it are rejected. "
    "The team will revisit the limit next quarter."
)


def _chunk(content: str) -> ChunkResult:
    return ChunkResult(
        content=content,
        token_count=len(content.split()),
        chunk_index=0,
        total_chunks=1,
        chunk_type="text",
        metadata={},
    )


def _facts_response(facts: list[str]) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(facts))]
    response.usage = MagicMock(input_tokens=100, output_tokens=50)
    return response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_thin_content_skips_extraction():
    """A single thin chunk becomes one memory, verbatim, with no model call."""
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_facts_response(["never used"]))

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        result = await FactExtractor(client).extract(
            [_chunk(ATTRACTOR)], content_type="text"
        )

    client.messages.create.assert_not_awaited()
    assert result.facts == [ATTRACTOR], (
        "the original line must be preserved verbatim, not paraphrased"
    )
    assert result.total_chunks == 1
    assert result.failed_chunks == 0
    # Skipping is not failing: the document must still complete normally.
    assert result.wholly_failed is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_long_content_still_uses_extraction():
    """The 2647-char case from the diagnostic must be unaffected."""
    # Deliberately distinct: SBERT deduplication runs after extraction and
    # would collapse near-identical placeholders, which is correct behaviour
    # but would make this assert the wrong thing.
    facts = [
        "FragmentInstance.addEventListener fails to cross-match boolean capture options.",
        "The DOM Living Standard treats addEventListener(type, fn, true) as identical to {capture: true}.",
        "normalizeListenerOptions in ReactFiberConfigDOM.js builds the listener dedup key.",
        "The boolean branch encodes only the capture flag as c=1 or c=0.",
        "The object branch encodes capture, once and passive flags.",
        "Mismatched capture formats leak listeners on Fragment child DOM nodes.",
        "Commit 142cfde89eda was authored by Dhakshin2007 under PR #36047.",
        "Two regression tests were added to ReactDOMFragmentRefs-test.js.",
    ]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_facts_response(facts))

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        result = await FactExtractor(client).extract(
            [_chunk(LONG_SOURCE)], content_type="text"
        )

    client.messages.create.assert_awaited_once()
    assert len(result.facts) >= 5, "long content must yield multiple distinct facts"
    assert LONG_SOURCE not in result.facts, "long content must be decomposed, not echoed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_thin_but_multi_sentence_content_still_extracts():
    """Short is not the same as atomic.

    Three separate claims in 120 characters must still be decomposed; skipping
    on length alone would collapse them into a single memory.
    """
    assert len(SHORT_MULTI) < _THIN_CONTENT_CHARS, "fixture must be under the threshold"

    facts = [
        "The maximum upload size is 50 megabytes.",
        "Requests above 50 megabytes are rejected.",
        "The team will revisit the upload limit next quarter.",
    ]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_facts_response(facts))

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        result = await FactExtractor(client).extract(
            [_chunk(SHORT_MULTI)], content_type="text"
        )

    client.messages.create.assert_awaited_once(), "multi-sentence content must extract"
    assert result.facts == facts


@pytest.mark.unit
@pytest.mark.asyncio
async def test_thin_trailing_chunk_of_a_long_document_still_extracts():
    """The skip applies to whole documents, not to fragments.

    A long document can end in a short chunk. Embedding that fragment alone
    would be worse than extracting from it, so the skip requires the document
    to be a single chunk.
    """
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_facts_response(["a fact"]))

    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        result = await FactExtractor(client).extract(
            [_chunk(LONG_SOURCE), _chunk(ATTRACTOR)], content_type="text"
        )

    assert client.messages.create.await_count == 2, (
        "both chunks must be extracted; the thin one is a fragment, not a document"
    )
    assert ATTRACTOR not in result.facts

@pytest.mark.unit
@pytest.mark.asyncio
async def test_long_content_cache_read_and_write_are_unaffected():
    """The skip must not disturb caching for content that still extracts.

    The skip returns before _extract_from_chunk, which is where the cache lives,
    so thin content never consults Redis. That is intended, but it must apply
    ONLY to thin content: the extraction path is the expensive one, and losing
    its cache would mean re-paying for every chunk of every long document.
    """
    facts = [
        "FragmentInstance.addEventListener fails to cross-match boolean capture options.",
        "Commit 142cfde89eda was authored by Dhakshin2007 under PR #36047.",
    ]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_facts_response(facts))

    # ── cold: cache missed, so the API is called and the result is stored ──
    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        cold = await FactExtractor(client).extract(
            [_chunk(LONG_SOURCE)], content_type="text"
        )

        redis.return_value.get.assert_awaited(), "cache must be consulted"
        redis.return_value.setex.assert_awaited_once(), "result must be cached"
        cached_payload = redis.return_value.setex.await_args.args[2]

    assert client.messages.create.await_count == 1
    assert json.loads(cached_payload) == cold.facts

    # ── warm: the stored value is served without touching the API ──
    warm_client = AsyncMock()
    warm_client.messages.create = AsyncMock(return_value=_facts_response(["unused"]))
    with patch("sourcemind.services.ingestion.fact_extractor.get_redis") as redis:
        redis.return_value.get = AsyncMock(return_value=cached_payload)
        redis.return_value.setex = AsyncMock()

        warm = await FactExtractor(warm_client).extract(
            [_chunk(LONG_SOURCE)], content_type="text"
        )

    warm_client.messages.create.assert_not_awaited(), "a cache hit must skip the API"
    assert warm.facts == cold.facts
    assert warm.failed_chunks == 0
