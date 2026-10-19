"""End-to-end journey of automatic ingestion, against real PostgreSQL and Redis.

connect -> import -> source update -> automatic sync -> natural-language search -> citation,
plus the failure cases the plan lists (mid-sync failure, repeated delivery, other workspace,
disconnect).

REAL here: the connector orchestrator, scope discovery, mapper, GitHub client (HTTP semantics
over httpx.MockTransport), the receiver and its outbox, the ingestion worker pipeline stages
that persist (publication, revision heads, retirement, attribution), the read/search service
and Postgres under FORCE RLS as the restricted runtime role.

SIMULATED (so this is NOT proof of live GitHub, live model providers or retrieval quality):
GitHub (an in-memory fake), fact extraction (sentence split), embeddings (hashed
bag-of-words), the access verifier (always authorized), the Celery broker (the test runs the
worker function directly).
"""

# ruff: noqa: F811

from __future__ import annotations

import hashlib
import math
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from sourcemind.connectors.github.connector import GitHubConnector
from sourcemind.core.redis_client import init_redis
from sourcemind.models.connector import ConnectorConfig
from sourcemind.services.ingestion.chunker import ChunkResult
from sourcemind.services.ingestion.embedder import EmbeddingResult
from sourcemind.services.ingestion.extractor import ExtractionResult as DocExtraction
from sourcemind.services.ingestion.fact_extractor import ExtractionResult as FactResult
from sourcemind.services.memory.read import search_memories_for_user
from tests.fakes.fake_github import FakeGitHub, make_client
from tests.integration.test_connector_sync_real_db import FakeVerifier, _open, _settings
from tests.integration.test_github_author_links_real_db import _gid, _put_link, _run_worker
from tests.integration.test_ingestion_worker_contract import (  # noqa: F401 - fixtures
    WorkerHarness,
    ingestion_worker_database_url,
    ingestion_worker_engine,
    ingestion_worker_environment,
    ingestion_worker_redis_url,
    worker_harness,
)
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REPO = "acme/repo"
ISSUE_BODY = "We cache user sessions in Redis with a one hour TTL because Postgres lookups were slow."
ISSUE_BODY_V2 = "We cache user sessions in Redis with a two hour TTL because Postgres lookups were slow."
COMMENT = "Correction: the session cache TTL is thirty minutes, not one hour."
COMMIT = "Raise the database connection pool size to twenty to stop timeouts under load."


# ── simulated model stages ───────────────────────────────────────────────────

_STOP = {"the", "a", "an", "we", "do", "how", "is", "are", "in", "to", "of", "and", "for", "our"}


def _embedding(text_: str) -> list[float]:
    """Deterministic hashed bag of word prefixes: overlapping vocabulary = high cosine."""
    vec = [0.0] * 3072
    for token in re.findall(r"[a-z0-9]+", text_.lower()):
        if token in _STOP:
            continue
        vec[int(hashlib.md5(token[:5].encode()).hexdigest(), 16) % 3072] += 1.0  # noqa: S324
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


@pytest.fixture
def simulated_models(worker_harness: WorkerHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    from sourcemind.services.ingestion import chunker, embedder, extractor, fact_extractor
    from sourcemind.services.search import hybrid

    async def extract(*, content: str, **_kw: object) -> DocExtraction:
        return DocExtraction(content=content, content_type="text")

    async def chunk(extraction: DocExtraction) -> list[ChunkResult]:
        return [
            ChunkResult(
                content=extraction.content,
                token_count=max(1, len(extraction.content.split())),
                chunk_index=0,
                total_chunks=1,
                chunk_type="text",
            )
        ]

    async def facts(chunks: list[ChunkResult], **_kw: object) -> FactResult:
        sentences = [
            s.strip()
            for c in chunks
            for s in re.split(r"(?<=[.!?])\s+", c.content)
            if len(s.strip()) > 10
        ]
        return FactResult(facts=sentences, total_chunks=len(chunks), failed_chunks=0, failure_reasons=[])

    async def embed(items: list[str]) -> list[EmbeddingResult]:
        return [
            EmbeddingResult(content=i, embedding=_embedding(i), token_count=8, cached=False)
            for i in items
        ]

    async def query_embedding(query: str, _client: object | None):
        return _embedding(query), True

    monkeypatch.setattr(extractor, "extract", extract)
    monkeypatch.setattr(chunker, "chunk", chunk)
    monkeypatch.setattr(
        fact_extractor, "FactExtractor", lambda _c: SimpleNamespace(extract=facts)
    )
    monkeypatch.setattr(embedder, "EmbeddingService", lambda _c: SimpleNamespace(embed=embed))
    monkeypatch.setattr(hybrid, "_get_query_embedding", query_embedding)


# ── world ────────────────────────────────────────────────────────────────────


class World:
    def __init__(self, engine: AsyncEngine, data: SecurityTenantData, cid: uuid.UUID,
                 fake: FakeGitHub) -> None:
        self.engine, self.data, self.cid, self.fake = engine, data, cid, fake
        self.verifier = FakeVerifier()

    async def sync(self, trigger: str = "scheduled"):
        """One scheduled run of the real orchestrator against the fake GitHub."""
        await init_redis()  # the worker closes the process-wide client after each run
        session = await _open(self.engine, self.data.admin_id, self.data.target_workspace_id)
        async with session:
            connector = GitHubConnector(
                config=SimpleNamespace(id=self.cid),
                auth=SimpleNamespace(_installation_id="4242"),
                session=session,
                workspace_id=self.data.target_workspace_id,
                user_id=self.data.admin_id,
                client=make_client(self.fake),
                verifier=self.verifier,
                settings=_settings(),
                clock=lambda: self.fake.now,  # discovery windows follow the fake GitHub's time
            )
            return await connector.run(trigger=trigger)

    async def rows(self, sql: str, **params):
        session = await _open(self.engine, self.data.owner_id, self.data.target_workspace_id)
        async with session:
            return list((await session.execute(text(sql), params)).fetchall())

    async def drain_worker(self) -> int:
        """Run the ingestion worker for every pending connector document, oldest first."""
        pending = await self.rows(
            "SELECT id FROM documents WHERE connector_id = :c AND ingestion_status = 'pending' "
            "ORDER BY created_at, id",
            c=self.cid,
        )
        for (doc_id,) in pending:
            await init_redis()
            result = await _run_worker(self.data, doc_id, self.data.admin_id)
            assert result["status"] == "completed", result
        return len(pending)

    async def search(self, query: str, *, user: uuid.UUID | None = None,
                     workspace: uuid.UUID | None = None, mode: str = "hybrid"):
        session = await _open(self.engine, user or self.data.member_id, workspace or self.data.target_workspace_id)
        async with session:
            with patch("sourcemind.services.memory.read.enforce_rate_limit", new=AsyncMock()):
                return await search_memories_for_user(
                    session,
                    user_id=user or self.data.member_id,
                    workspace_id=workspace or self.data.target_workspace_id,
                    query=query,
                    mode=mode,
                    limit=10,
                )


@pytest.fixture
async def world(
    ingestion_worker_engine: AsyncEngine,
    simulated_models: None,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    from sourcemind.workers import ingestion as worker

    monkeypatch.setattr(
        worker.process_document, "apply_async", lambda **kw: SimpleNamespace(id=kw["task_id"])
    )
    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    cid = uuid.uuid4()
    session = await _open(engine, data.owner_id, data.target_workspace_id)
    async with session:
        session.add(
            ConnectorConfig(
                id=cid, workspace_id=data.target_workspace_id, connector_type="github",
                display_name=f"journey-{cid.hex[:8]}", config={"repos": [REPO]},
                status="active", installation_id=4242, authorized_github_user_id=777,
                created_by=data.admin_id,
            )
        )
        await session.commit()

    fake = FakeGitHub(now=datetime.now(UTC) - timedelta(days=2))
    fake.add_repo(REPO)
    return World(engine, data, cid, fake)


def _texts(outcome) -> dict[str, object]:
    return {r.memory.content: r.memory for r in outcome.results}


# ── the journey ──────────────────────────────────────────────────────────────


async def test_connect_import_edit_sync_search_and_cite(world: World) -> None:
    fake, data = world.fake, world.data
    alice, bob = _gid(), _gid()
    fake.user("alice", alice)
    fake.user("bob", bob)
    fake.user("carol")
    # alice is linked to a workspace member by an admin; bob and carol are not.
    await _put_link(world.engine, data, data.admin_id, data.target_workspace_id, alice, data.member_id)

    issue = fake.add_issue(REPO, "alice", "Session caching", ISSUE_BODY)
    fake.add_issue_comment(REPO, issue, "bob", COMMENT)
    fake.add_commit(REPO, COMMIT, "carol")

    # 1-2. connect + import without pasting anything
    first = await world.sync()
    assert first.status == "completed", first
    assert first.counts["new"] == 3 and first.counts["failed"] == 0, (first.status, first.error_code, first.counts, await world.rows("select repo, artifact_type, status, last_error_code from connector_sync_cursors where connector_id=:c", c=world.cid), world.fake.log[-6:])
    assert await world.drain_worker() == 3
    assert world.verifier.calls, "access was verified before fetching content"

    # 3. memories keep source links, artifact identity and the REAL author, not the connector admin
    out = await world.search("how do we cache user sessions?")
    by_text = _texts(out)
    assert ISSUE_BODY in by_text and COMMENT in by_text  # exact authored text, no labels added
    commit_hit = _texts(await world.search("what did we change about the database connection pool?"))
    assert COMMIT in commit_hit
    m = by_text[ISSUE_BODY]
    assert m.source.url == f"https://github.com/{REPO}/issues/{issue}"
    assert m.source.type == "issue" and m.source.id == f"{REPO}/issues/{issue}"
    assert m.authorship.status == "resolved"  # alice -> linked member
    c = by_text[COMMENT]
    assert c.source.type == "issue_comment" and "issuecomment" in c.source.url
    assert c.authorship.status == "unresolved" and c.authorship.source_author == "bob"
    k = commit_hit[COMMIT]
    assert k.source.type == "commit" and k.authorship.status == "unresolved"
    # nobody was credited just for connecting/syncing
    credited = await world.rows(
        "SELECT DISTINCT user_id FROM attributions a JOIN memories m ON m.id = a.memory_id "
        "WHERE m.workspace_id = :w AND m.document_id IN "
        "(SELECT id FROM documents WHERE connector_id = :c)",
        w=data.target_workspace_id, c=world.cid,
    )
    assert data.admin_id not in {r[0] for r in credited}

    # 8. natural-language question (no shared identifier) ranks the relevant memory first
    top = (await world.search("how long are user sessions cached?")).results[0].memory
    assert top.content in {ISSUE_BODY, COMMENT}

    # 5. repeated delivery changes nothing
    again = await world.sync()
    assert again.counts["new"] == 0 and again.counts["updated"] == 0
    assert await world.drain_worker() == 0
    assert set(_texts(await world.search("how do we cache user sessions?"))) == set(by_text)

    # 4. the source changes -> the NEXT scheduled run picks it up automatically
    fake.tick(120)
    fake.edit_item(REPO, issue, body=ISSUE_BODY_V2)
    updated = await world.sync()
    assert updated.counts["updated"] == 1 and updated.counts["new"] == 0, updated.counts
    assert await world.drain_worker() == 1
    after = _texts(await world.search("how do we cache user sessions?"))
    assert ISSUE_BODY_V2 in after and ISSUE_BODY not in after  # old revision retired, new live
    assert after[ISSUE_BODY_V2].source.revision != m.source.revision
    assert after[ISSUE_BODY_V2].source.revision_seq == 2
    assert COMMENT in after  # untouched artifacts unaffected
    assert COMMIT in _texts(await world.search("database connection pool"))
    # history is kept: the retired revision is still in the database with its reason
    retired = await world.rows(
        "SELECT deleted_reason FROM memories WHERE content = :c", c=ISSUE_BODY
    )
    assert [r[0] for r in retired] == ["source_superseded"]


async def test_mid_sync_failure_recovers_without_skipping_artifacts(world: World) -> None:
    fake = world.fake
    fake.user("alice")
    first_issue = fake.add_issue(REPO, "alice", "One", "First issue body for the journey test.")
    fake.add_issue(REPO, "alice", "Two", "Second issue body for the journey test.")
    fake.add_commit(REPO, COMMIT, "alice")

    fake.inject("/commits", "500", times=None)  # the commit feed is down
    from sourcemind.connectors.github.contracts import TransientError

    with pytest.raises(TransientError):  # re-raised on purpose: the Celery task retries it
        await world.sync()
    failed_logs = await world.rows(
        "SELECT status, error_code FROM connector_sync_logs WHERE connector_id = :c", c=world.cid
    )
    assert failed_logs and failed_logs[-1][0] == "failed" and failed_logs[-1][1] is not None

    fake.clear_rules()
    fake.tick(30)
    healed = await world.sync()
    assert healed.status == "completed", healed
    await world.drain_worker()
    ids = {r[0] for r in await world.rows(
        "SELECT a.source_id FROM artifact_links a JOIN documents d ON d.id = a.document_id "
        "WHERE d.connector_id = :c", c=world.cid)}
    assert f"{REPO}/issues/{first_issue}" in ids and f"{REPO}/issues/{first_issue + 1}" in ids
    assert any("/commits/" in i for i in ids)  # nothing skipped, nothing duplicated
    dupes = await world.rows(
        "SELECT source_id, count(*) FROM artifact_links WHERE source_id LIKE :p "
        "GROUP BY source_id, revision_seq HAVING count(*) > 1", p=f"{REPO}/%")
    assert dupes == []


async def test_another_workspace_sees_nothing_of_the_connector_or_its_memories(world: World) -> None:
    fake, data = world.fake, world.data
    fake.user("alice")
    fake.add_issue(REPO, "alice", "Session caching", ISSUE_BODY)
    await world.sync()
    await world.drain_worker()

    # a member of the OTHER workspace, naming the other workspace, sees none of it
    other = await world.search(
        "cache sessions", user=data.other_workspace_member_id, workspace=data.other_workspace_id
    )
    assert other.results == []
    from sourcemind.core.exceptions import WorkspaceAccessDeniedError, WorkspaceNotFoundError
    with pytest.raises((WorkspaceAccessDeniedError, WorkspaceNotFoundError)):
        await world.search("cache sessions", user=data.other_workspace_member_id)
    rows = await world.rows("SELECT count(*) FROM connector_configs WHERE id = :c", c=world.cid)
    assert rows[0][0] == 1
    session = await _open(world.engine, data.other_workspace_member_id, data.other_workspace_id)
    async with session:
        assert (await session.execute(text("SELECT count(*) FROM connector_configs"))).scalar() == 0
        assert (await session.execute(text("SELECT count(*) FROM artifact_links"))).scalar() == 0


async def test_disconnect_stops_future_sync_and_queued_publication(world: World) -> None:
    from sourcemind.services.connectors import lifecycle

    fake, data = world.fake, world.data
    fake.user("alice")
    fake.add_issue(REPO, "alice", "Session caching", ISSUE_BODY)
    assert (await world.sync()).counts["new"] == 1  # handed off, NOT yet processed

    session = await _open(world.engine, data.admin_id, data.target_workspace_id)
    async with session:
        from sourcemind.services.memory.retire import retire_document_memories

        await lifecycle.disconnect(
            session, world.cid, remove_memories=False, retire=retire_document_memories
        )
        await session.commit()

    # the queued document must not publish, and no provider call may be made for it
    from sourcemind.workers.ingestion import _run_pipeline
    from tests.integration.test_ingestion_worker_contract import FakeTask

    pending = await world.rows(
        "SELECT id FROM documents WHERE connector_id = :c AND ingestion_status = 'pending'",
        c=world.cid,
    )
    await init_redis()
    result = await _run_pipeline(
        FakeTask(retries=0), str(pending[0][0]), str(data.target_workspace_id), str(data.admin_id)
    )
    assert result["status"] == "failed"
    published = await world.rows(
        "SELECT count(*) FROM memories WHERE document_id IN "
        "(SELECT id FROM documents WHERE connector_id = :c)", c=world.cid)
    assert published[0][0] == 0

    # and a later scheduled run does no work at all
    fake.tick(60)
    fake.add_issue(REPO, "alice", "Another", "A later issue that must never be imported.")
    calls_before = len(fake.content_calls())
    after = await world.sync()
    assert after.status != "completed" or after.counts.get("new", 0) == 0
    assert len(fake.content_calls()) == calls_before
