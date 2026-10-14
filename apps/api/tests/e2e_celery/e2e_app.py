"""Celery app for the real-worker end-to-end run.

The REAL Celery application is used unchanged (broker, routes, acks_late, prefork, the tasks and the
whole sync/ingestion code). Only the outside world is replaced, and it is replaced here, in the
worker process, so nothing in the code under test knows about it:

* GitHub: the in-memory ``FakeGitHub`` behind the real ``GitHubClient`` (SIMULATED)
* model providers: extraction, chunking, fact extraction, embeddings, relation detection (SIMULATED)
* GitHub App auth / repository verifier: stubbed to "authorized"

Failure injection is by file, so a test can freeze a worker at an exact point and then kill it:
``$E2E_PROVIDER_HANG`` makes the model calls (extraction and embedding) wait, ``$E2E_GITHUB_HANG`` makes every GitHub request wait.
``$E2E_PROVIDER_LOG`` records one line per fact-extraction call (so "no provider call happened" is
checkable). Never import this module outside the end-to-end run.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx

from sourcemind.workers.celery_app import app  # noqa: F401 - the real application

HANG_PROVIDER = Path(os.environ.get("E2E_PROVIDER_HANG", "/tmp/e2e_provider_hang"))
HANG_GITHUB = Path(os.environ.get("E2E_GITHUB_HANG", "/tmp/e2e_github_hang"))
PROVIDER_LOG = Path(os.environ.get("E2E_PROVIDER_LOG", "/tmp/e2e_provider_calls"))
REPO = "acme/repo"

# The production default visibility timeout is one hour: a worker killed mid-task (OOM, node loss)
# has its unacknowledged message redelivered only after that. Shortened here so the behaviour can be
# observed in a test; see the end-to-end notes in the report.
app.conf.broker_transport_options = {
    "visibility_timeout": int(os.environ.get("E2E_VISIBILITY_TIMEOUT", "5")),
    "unacked_mutex_expire": 5,
}


def _install_providers() -> None:
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    from sourcemind.services.attribution import scorer as attribution_scorer
    from sourcemind.services.ingestion import chunker, embedder, extractor, fact_extractor
    from sourcemind.services.ingestion.chunker import ChunkResult
    from sourcemind.services.ingestion.embedder import EmbeddingResult
    from sourcemind.services.ingestion.extractor import ExtractionResult as DocExtraction
    from sourcemind.services.ingestion.fact_extractor import ExtractionResult as FactExtraction
    from sourcemind.services.memory import relations

    async def extract_document(*, content: str, **_kw: object) -> DocExtraction:
        return DocExtraction(content=content, content_type="text")

    async def chunk_document(extraction: DocExtraction) -> list[ChunkResult]:
        return [
            ChunkResult(
                content=extraction.content, token_count=8, chunk_index=0, total_chunks=1,
                chunk_type="text",
            )
        ]

    async def extract_facts(chunks: list, *_a: object, **_kw: object) -> FactExtraction:
        with PROVIDER_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} extract_facts\n")
        waited = 0.0
        while HANG_PROVIDER.exists() and waited < 120:
            await asyncio.sleep(0.2)
            waited += 0.2
        text_ = chunks[0].content if chunks else "fact"
        return FactExtraction(
            facts=[f"Decision recorded: {text_[:120]}"],
            total_chunks=1, failed_chunks=0, failure_reasons=[],
            usage={"calls": 1, "input_tokens": 1, "output_tokens": 1, "estimated": False},
        )

    async def embed(facts: list[str]) -> list[EmbeddingResult]:
        # Short segments take the verbatim path and never reach fact extraction, but every path
        # embeds, so this is the provider call the failure injection and the call log hook into.
        with PROVIDER_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()} embed\n")
        waited = 0.0
        while HANG_PROVIDER.exists() and waited < 120:
            await asyncio.sleep(0.2)
            waited += 0.2
        return [
            EmbeddingResult(content=f, embedding=[0.01] * 3072, token_count=8, cached=False)
            for f in facts
        ]

    async def no_relations(*_a: object, **_kw: object) -> list[object]:
        return []

    attribution_scorer.get_scorer = lambda: SimpleNamespace(  # type: ignore[assignment]
        compute_scores=lambda _edits: [
            SimpleNamespace(
                contribution_weight=1.0, char_diff_score=1.0, semantic_score=1.0,
                temporal_score=1.0, structural_score=0.0, approval_score=0.0,
            )
        ]
    )
    extractor.extract = extract_document  # type: ignore[assignment]
    chunker.chunk = chunk_document  # type: ignore[assignment]
    fact_extractor.FactExtractor = lambda _c: SimpleNamespace(extract=extract_facts)  # type: ignore
    embedder.EmbeddingService = lambda _c: SimpleNamespace(embed=embed)  # type: ignore
    relations.RelationDetector = lambda _c: SimpleNamespace(  # type: ignore
        detect=no_relations, plan=no_relations, apply=no_relations
    )
    AsyncOpenAI.__new__ = lambda cls, **_kw: object()  # type: ignore[assignment,method-assign]
    AsyncAnthropic.__new__ = lambda cls, **_kw: object()  # type: ignore[assignment,method-assign]


def _install_github() -> None:
    from sourcemind.connectors.github import connector as gh_connector
    from sourcemind.connectors.github.contracts import AccessVerdict, VerifierResult
    from sourcemind.workers import connector_tasks
    from tests.fakes.fake_github import FakeGitHub, make_client

    fake = FakeGitHub(now=datetime.now(UTC) - timedelta(minutes=30))
    fake.add_repo(REPO)
    for i in range(int(os.environ.get("E2E_ISSUES", "2"))):
        fake.add_issue(REPO, "octocat", f"issue {i}", f"Cache decision number {i}: use Redis.")

    # One commit so the commits scope has a head; two issues + one commit = three documents.
    fake.add_commit(REPO, "Switch the cache to Redis", "octocat")

    inner = fake._handle

    async def handler(request: httpx.Request) -> httpx.Response:
        waited = 0.0
        while HANG_GITHUB.exists() and waited < 120:
            await asyncio.sleep(0.2)
            waited += 0.2
        return inner(request)

    fake.transport = httpx.MockTransport(handler)

    class Verifier:
        async def verify(self, *, owner: str, repo: str, github_user_id: int) -> VerifierResult:
            return VerifierResult(AccessVerdict.AUTHORIZED, "authorized")

    gh_connector.GitHubClient = lambda _auth: make_client(fake)  # type: ignore[assignment]
    connector_tasks.GitHubAppAuth = lambda **_kw: SimpleNamespace()  # type: ignore[assignment]
    connector_tasks._build_verifier = lambda _auth: Verifier()  # type: ignore[assignment]


_install_providers()
_install_github()
