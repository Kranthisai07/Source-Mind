"""An empty extraction and a failed one must not look the same.

Four conditions used to return a bare []: the model genuinely returning an
empty array, a response that parsed but was not a list, unparseable JSON, and
an API error. The pipeline treated all four identically — document COMPLETED,
memory_count 0, error_message NULL — so a total extraction failure was
recorded as a successfully processed empty document.

That mattered in practice. In the 300-item evaluation 56 documents produced no
memories; some were spam and unfilled templates, and some were substantive
content whose extraction had simply flaked, since re-running it produced real
facts. There was no way to tell which was which.

Two things follow, and are tested here: anything other than facts is attempted
twice, and the outcome is reported rather than flattened.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.services.ingestion.chunker import ChunkResult
from sourcemind.services.ingestion.fact_extractor import FactExtractor


# Multi-sentence on purpose. These tests are about retry, parse failure and
# caching; multi-sentence content exercises those paths unambiguously and keeps
# the fixture honest about what is being verified. (It was originally widened
# to dodge D-001's thin-content skip, which D-003 reverted - the width is kept
# because it is the better fixture, not because it is still required.)
_EXTRACTABLE = (
    "The ingestion pipeline stores memories in PostgreSQL. Retrieval fuses "
    "pgvector similarity with BM25 ranking. The fusion uses Reciprocal Rank "
    "Fusion with a smoothing constant of 60."
)


def _chunk(content: str = _EXTRACTABLE) -> ChunkResult:
    return ChunkResult(
        content=content,
        token_count=len(content.split()),
        chunk_index=0,
        total_chunks=1,
        chunk_type="text",
        metadata={},
    )


def _response(payload: str) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=payload)]
    response.usage = MagicMock(input_tokens=100, output_tokens=50)
    return response


def _facts_response(facts: list[str]) -> MagicMock:
    return _response(json.dumps(facts))


def _redis_miss():
    """A Redis stub that never has a cached value."""
    stub = patch("sourcemind.services.ingestion.fact_extractor.get_redis")
    return stub


@pytest.mark.unit
@pytest.mark.asyncio
async def test_genuine_empty_extraction_accepted_after_one_retry():
    """Two valid empty answers mean the content genuinely has no facts.

    The retry is the point: accepting the first [] is what silently dropped
    content whose extraction had flaked. Two independent empties is the
    evidence needed before calling a document empty.
    """
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_facts_response([]))

    with _redis_miss() as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        result = await FactExtractor(client).extract([_chunk()], content_type="text")

    assert result.facts == []
    # Empty is NOT a failure.
    assert result.failed_chunks == 0
    assert result.wholly_failed is False

    assert client.messages.create.await_count == 2, (
        "the first empty must be retried before being accepted"
    )
    # A confirmed empty is cached, so the second ingestion of identical
    # content does not pay for two more calls.
    redis.return_value.setex.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transient_failure_recovered_on_retry():
    """An empty first answer followed by real facts must use the facts.

    This is the case that was silently losing content, and the one that
    quantifies how much of an apparently empty corpus is flakiness.
    """
    client = AsyncMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _facts_response([]),
            _facts_response(["The API rate limit is 500 requests per second."]),
        ]
    )

    with _redis_miss() as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        result = await FactExtractor(client).extract([_chunk()], content_type="text")

    assert result.facts == ["The API rate limit is 500 requests per second."], (
        "the retry's facts must be used, not the first attempt's empty result"
    )
    assert result.failed_chunks == 0
    assert client.messages.create.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parse_failure_distinguished_from_genuine_empty():
    """Malformed JSON and a valid empty array must produce different states.

    Both yield zero facts. Only one of them is a defect, and the pipeline
    decides whether to mark the document COMPLETED or FAILED on exactly this
    distinction.
    """
    # Valid, parsed, empty.
    empty_client = AsyncMock()
    empty_client.messages.create = AsyncMock(return_value=_facts_response([]))

    # Not JSON at all.
    broken_client = AsyncMock()
    broken_client.messages.create = AsyncMock(
        return_value=_response("I could not find any facts in this document.")
    )

    with _redis_miss() as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        empty = await FactExtractor(empty_client).extract([_chunk()], content_type="text")
        broken = await FactExtractor(broken_client).extract([_chunk()], content_type="text")

    # Same facts...
    assert empty.facts == broken.facts == []

    # ...different states. This is the whole point.
    assert empty.failed_chunks == 0
    assert empty.wholly_failed is False

    assert broken.failed_chunks == 1
    assert broken.wholly_failed is True
    assert "json_error" in broken.failure_reasons[0]

    # A failure is never cached — replaying it for seven days as though it
    # were an answer is how one bad response becomes a permanent gap.
    assert redis.return_value.setex.await_count == 1, (
        "only the genuine empty should have been cached"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_api_error_still_retried_and_logged_loudly():
    """An API error is retried, and reported as a failure rather than empty."""
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("API timeout"))

    with _redis_miss() as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        result = await FactExtractor(client).extract([_chunk()], content_type="text")

    assert result.facts == []
    assert result.failed_chunks == 1
    assert result.wholly_failed is True
    assert "api_error" in result.failure_reasons[0]
    assert "API timeout" in result.failure_reasons[0]

    assert client.messages.create.await_count == 2, "the API error must be retried"
    redis.return_value.setex.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_api_error_then_success_is_recovered():
    """A failed first call followed by a good one still yields the facts."""
    client = AsyncMock()
    client.messages.create = AsyncMock(
        side_effect=[
            RuntimeError("connection reset"),
            _facts_response(["Postgres 18 is the primary datastore."]),
        ]
    )

    with _redis_miss() as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        result = await FactExtractor(client).extract([_chunk()], content_type="text")

    assert result.facts == ["Postgres 18 is the primary datastore."]
    assert result.failed_chunks == 0
    assert result.wholly_failed is False

# A long, multi-chunk source. Kept verbatim from the evaluation corpus so the
# cache test exercises realistic content rather than a synthetic string.
_LONG_SOURCE = (
    "[facebook/react] Commit 142cfde89eda by Dhakshin2007: Fix FragmentInstance "
    "listener leak: normalize boolean vs object capture options per DOM spec "
    "(#36047)\n\n## Summary\n\n`FragmentInstance.addEventListener` and "
    "`removeEventListener` fail to cross-match listeners when the `capture` "
    "option is passed as a boolean in one call and an options object in the "
    "other. This violates the DOM Living Standard, which specifies that "
    "addEventListener(type, fn, true) and addEventListener(type, fn, "
    "{capture: true}) are identical. The result is a listener leak."
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_is_read_and_written_for_extracted_content():
    """Extraction is the expensive path, so its cache has to work.

    Losing this cache would mean re-paying the model for every chunk of every
    document on re-ingestion. A cold call must consult Redis, call the API once
    and store the result; a warm call must serve the stored value without
    touching the API.

    (Relocated from test_thin_content_skip.py, deleted with D-003. The rest of
    that file asserted the reverted skip behaviour; this test does not depend
    on it and is still true.)
    """
    facts = [
        "FragmentInstance.addEventListener fails to cross-match boolean capture options.",
        "Commit 142cfde89eda was authored by Dhakshin2007 under PR #36047.",
    ]
    client = AsyncMock()
    client.messages.create = AsyncMock(return_value=_facts_response(facts))

    # ── cold: cache missed, so the API is called and the result stored ──
    with _redis_miss() as redis:
        redis.return_value.get = AsyncMock(return_value=None)
        redis.return_value.setex = AsyncMock()

        cold = await FactExtractor(client).extract(
            [_chunk(_LONG_SOURCE)], content_type="text"
        )

        redis.return_value.get.assert_awaited(), "cache must be consulted"
        redis.return_value.setex.assert_awaited_once(), "result must be cached"
        cached_payload = redis.return_value.setex.await_args.args[2]

    assert client.messages.create.await_count == 1
    assert json.loads(cached_payload) == cold.facts

    # ── warm: the stored value is served without touching the API ──
    warm_client = AsyncMock()
    warm_client.messages.create = AsyncMock(return_value=_facts_response(["unused"]))
    with _redis_miss() as redis:
        redis.return_value.get = AsyncMock(return_value=cached_payload)
        redis.return_value.setex = AsyncMock()

        warm = await FactExtractor(warm_client).extract(
            [_chunk(_LONG_SOURCE)], content_type="text"
        )

    warm_client.messages.create.assert_not_awaited(), "a cache hit must skip the API"
    assert warm.facts == cold.facts
    assert warm.failed_chunks == 0
