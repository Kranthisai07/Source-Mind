"""FactExtractor: concurrency bound, non-authored context, token usage (no network).

The provider client is a stub; Redis and the SBERT dedup are replaced.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.services.ingestion import fact_extractor
from sourcemind.services.ingestion.chunker import ChunkResult
from sourcemind.services.ingestion.fact_extractor import FactExtractor, _cache_key


def _chunk(text: str, idx: int = 0) -> ChunkResult:
    return ChunkResult(
        content=text, token_count=3, chunk_index=idx, total_chunks=1, chunk_type="text",
        metadata={},
    )


def _response(facts: list[str], usage: object | None = ...) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=json.dumps(facts))]
    if usage is ...:
        usage = SimpleNamespace(input_tokens=11, output_tokens=7)
    response.usage = usage
    return response


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()
    monkeypatch.setattr(fact_extractor, "get_redis", lambda: redis)

    async def passthrough(facts: list[str]) -> list[str]:
        return facts

    monkeypatch.setattr(fact_extractor, "_deduplicate_facts", passthrough)
    return redis


def _settings(monkeypatch: pytest.MonkeyPatch, concurrency: int) -> None:
    from sourcemind.core import config

    monkeypatch.setattr(
        config, "get_settings", lambda: SimpleNamespace(extraction_concurrency=concurrency)
    )


@pytest.mark.unit
@pytest.mark.parametrize("limit", [1, 2, 4])
async def test_provider_calls_are_bounded_by_the_semaphore(monkeypatch, limit) -> None:
    _settings(monkeypatch, limit)
    active = 0
    peak = 0

    async def create(**_kwargs: object) -> MagicMock:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return _response(["A fact."])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    chunks = [_chunk(f"chunk {i}", i) for i in range(8)]
    result = await FactExtractor(client).extract(chunks)

    assert result.failed_chunks == 0 and len(result.facts) == 8
    assert peak == limit


@pytest.mark.unit
async def test_context_is_delimited_and_marked_as_not_authored(monkeypatch) -> None:
    _settings(monkeypatch, 4)
    prompts: list[str] = []

    async def create(**kwargs: object) -> MagicMock:
        prompts.append(kwargs["messages"][0]["content"])  # type: ignore[index]
        return _response(["A fact."])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    await FactExtractor(client).extract(
        [_chunk("the authored segment")], context="repo: acme/repo\nnumber: 7"
    )

    prompt = prompts[0]
    assert "<context>\nrepo: acme/repo\nnumber: 7\n</context>" in prompt
    assert "NOT authored text" in prompt
    assert "every fact must come from this text" in prompt
    assert prompt.index("</context>") < prompt.index("the authored segment")


@pytest.mark.unit
async def test_without_context_the_prompt_has_no_context_block(monkeypatch) -> None:
    _settings(monkeypatch, 4)
    prompts: list[str] = []

    async def create(**kwargs: object) -> MagicMock:
        prompts.append(kwargs["messages"][0]["content"])  # type: ignore[index]
        return _response(["A fact."])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    await FactExtractor(client).extract([_chunk("plain text")])
    assert "<context>" not in prompts[0] and "Content:\nplain text" in prompts[0]


@pytest.mark.unit
def test_context_participates_in_the_cache_key_only_when_present() -> None:
    assert _cache_key("x") == _cache_key("x", None)
    assert _cache_key("x", "repo: a") != _cache_key("x")
    assert _cache_key("x", "repo: a") != _cache_key("x", "repo: b")


@pytest.mark.unit
async def test_usage_is_reported_by_the_provider(monkeypatch) -> None:
    _settings(monkeypatch, 4)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=_response(["A fact."])))
    )
    result = await FactExtractor(client).extract([_chunk("a"), _chunk("b", 1)])
    assert result.usage == {
        "calls": 2, "input_tokens": 22, "output_tokens": 14, "estimated": False
    }


@pytest.mark.unit
async def test_usage_is_estimated_when_the_provider_does_not_report_it(monkeypatch) -> None:
    _settings(monkeypatch, 4)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=AsyncMock(return_value=_response(["A fact."], usage=None))
        )
    )
    result = await FactExtractor(client).extract([_chunk("some text to read")])
    assert result.usage["estimated"] is True
    assert result.usage["calls"] == 1 and result.usage["input_tokens"] > 0


@pytest.mark.unit
async def test_failed_attempts_count_toward_usage(monkeypatch) -> None:
    _settings(monkeypatch, 4)
    create = AsyncMock(side_effect=RuntimeError("provider down"))
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    with patch.object(fact_extractor, "log"):
        result = await FactExtractor(client).extract([_chunk("a")])
    assert result.failed_chunks == 1
    assert result.usage["calls"] == 2 and result.usage["estimated"] is True


@pytest.mark.unit
async def test_cache_hit_makes_no_call_and_reports_no_usage(monkeypatch, _isolated) -> None:
    _settings(monkeypatch, 4)
    _isolated.get = AsyncMock(return_value=json.dumps(["Cached fact."]))
    create = AsyncMock()
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    result = await FactExtractor(client).extract([_chunk("a")])
    assert result.facts == ["Cached fact."]
    assert create.await_count == 0
    assert result.usage["calls"] == 0
