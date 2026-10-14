"""The daily extraction-call budget counts every provider call and is shared by all tasks.

Real Redis (disposable), simulated providers. The reservation is the worst case of two calls
per chunk (the extractor retries an empty or failed answer once); the unused part is refunded
when the real call count is known, so retries are charged and a clean first attempt is not
over-charged.
"""

# ruff: noqa: F811

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from sourcemind.core.config import get_settings
from sourcemind.core.redis_client import get_redis, init_redis
from tests.integration.test_ingestion_worker_contract import (  # noqa: F401 - fixtures
    ingestion_worker_database_url,
    ingestion_worker_engine,
    ingestion_worker_environment,
    ingestion_worker_redis_url,
    worker_harness,
)
from tests.integration.test_revision_publication_real_db import (  # noqa: F401 - fixtures
    _fact,
    _facts,
    _handoff,
    _install_retire,
    _local_retire,
    _no_broker,
    _run,
    _sid,
    _text,
    world,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _key(ws: uuid.UUID) -> str:
    return f"extraction_budget:{ws}:{datetime.now(UTC):%Y%m%d}"


async def _counter(ws: uuid.UUID) -> int:
    await init_redis()  # the pipeline closes the process-global client when it ends
    return int(await get_redis().get(_key(ws)) or 0)


async def _doc_run(w, usage) -> dict:
    _facts(w.harness, [_fact()], usage=usage)
    doc = uuid.UUID(
        (await _handoff(w.engine, w.data, w.connector, _sid(), _text()))["document_id"]
    )
    return await _run(w.data, doc)


async def test_a_retried_extraction_is_charged_both_calls(world, monkeypatch) -> None:
    w = world
    monkeypatch.setenv("WORKSPACE_DAILY_EXTRACTION_BUDGET", "100")
    get_settings.cache_clear()
    _install_retire(w.mp, _local_retire)
    await init_redis()
    await get_redis().delete(_key(w.data.target_workspace_id))

    out = await _doc_run(w, {"calls": 2, "input_tokens": 1, "output_tokens": 1, "estimated": False})

    assert out["status"] == "completed"
    assert await _counter(w.data.target_workspace_id) == 2


async def test_a_clean_first_attempt_is_charged_one_call(world, monkeypatch) -> None:
    w = world
    monkeypatch.setenv("WORKSPACE_DAILY_EXTRACTION_BUDGET", "100")
    get_settings.cache_clear()
    _install_retire(w.mp, _local_retire)
    await init_redis()
    await get_redis().delete(_key(w.data.target_workspace_id))

    out = await _doc_run(w, {"calls": 1, "input_tokens": 1, "output_tokens": 1, "estimated": False})

    assert out["status"] == "completed"
    assert await _counter(w.data.target_workspace_id) == 1


async def test_parallel_reservations_never_exceed_the_limit() -> None:
    """Many tasks reserving at once share one atomic counter: exactly ``limit`` succeed."""
    from sourcemind.workers.ingestion import _reserve_budget

    await init_redis()
    ws = uuid.uuid4()
    limit = 10
    results = await asyncio.gather(*[_reserve_budget(ws, 1, limit) for _ in range(40)])
    assert sum(results) == limit
    assert await _counter(ws) == limit  # refused reservations were refunded
