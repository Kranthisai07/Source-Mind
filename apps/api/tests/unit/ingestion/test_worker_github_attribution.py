"""Worker credit-time recheck and finalization for GitHub-synced documents.

D-021. A GitHub document carries a sync-time decision in
``pipeline_data["attribution"]``. At credit time the worker:

  * re-reads the CURRENT admin-asserted link (``SELECT ... FOR SHARE``),
  * credits only if it still maps the numeric GitHub id to the SAME user that
    was recorded at sync time AND that user is still an active member,
  * otherwise records the document as unresolved and writes no attribution,
  * writes ``artifact_links.resolved_user_id`` for every link of the document
    (anchor + clones) in the same transaction, only when credited.

The sync initiator (the document submitter) is never credited for a GitHub
document. These tests drive the real ``_run_pipeline`` and the real
``RelationDetector`` against an in-memory fake session, stubbing only the
model provider, the scorer and the importance/severity rescoring.

Round 2: every provider call (fact extraction, embedding, relation
classification) must happen BEFORE the link lock; the lock is held only across
the database tail (recheck -> attribute -> backfill -> finalize -> apply).
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sourcemind.models.document import IngestionStatus


class RetryScheduled(RuntimeError):
    pass


class FakeTask:
    max_retries = 3

    def __init__(self, retries: int = 0) -> None:
        self.request = SimpleNamespace(retries=retries)

    def retry(self, *, exc: Exception, countdown: int) -> None:
        raise RetryScheduled(str(countdown))


@dataclass
class DbState:
    """Rows the fake session can see, snapshotted by savepoints."""

    links: list[dict[str, Any]] = field(default_factory=list)
    attributions: list[tuple[uuid.UUID, uuid.UUID]] = field(default_factory=list)
    final: dict[str, Any] | None = None
    status: str = IngestionStatus.PENDING
    writes: list[tuple[str, uuid.UUID, uuid.UUID]] = field(default_factory=list)


@dataclass
class Scenario:
    current_link_user: uuid.UUID | None = None
    member_active: bool = True
    link_lookup_error: bool = False
    memory_lookup_error: bool = False
    relation_error: bool = False
    link_sql: list[str] = field(default_factory=list)
    # Existing memory near every new one: (id, content, cosine distance).
    candidates: list[tuple[str, str, float]] = field(default_factory=list)
    candidate_contributor: uuid.UUID | None = None
    verdict: dict[str, Any] = field(
        default_factory=lambda: {"relation": "unrelated", "confidence": 0.0}
    )


class FakeTx:
    def __init__(self, session: FakeSession) -> None:
        self._session = session
        self._snapshot = copy.deepcopy(session.state)
        self.is_active = True

    def __await__(self):
        async def _self():
            return self

        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            await self.rollback()
        else:
            self.is_active = False
        return False

    async def rollback(self) -> None:
        self._session.state.__dict__.update(copy.deepcopy(self._snapshot).__dict__)
        self.is_active = False

    async def commit(self) -> None:
        self.is_active = False


class FakeSession:
    def __init__(self, doc: SimpleNamespace, state: DbState, scenario: Scenario) -> None:
        self.doc = doc
        self.state = state
        self.scenario = scenario
        self.info: dict[str, Any] = {}
        # Ordered log of side effects, NOT rolled back by savepoints.
        self.events: list[str] = []

    def add(self, obj: object) -> None:
        from sourcemind.models.memory_conflict import MemoryConflict
        from sourcemind.models.memory_relation import MemoryRelation

        if isinstance(obj, MemoryConflict):
            self.events.append("apply:conflict")
            self.state.writes.append(("conflict", obj.memory_a_id, obj.memory_b_id))
        elif isinstance(obj, MemoryRelation):
            self.events.append("apply:relation")
            self.state.writes.append(("relation", obj.source_memory_id, obj.target_memory_id))
        else:
            raise AssertionError(f"unexpected add: {type(obj).__name__}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin_nested(self) -> FakeTx:
        return FakeTx(self)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def scalar(self, *_a, **_k):
        return None

    async def execute(self, stmt, params: dict | None = None, **_kwargs):
        sql = " ".join(str(stmt).split())
        params = params or {}
        result = SimpleNamespace()
        if "github_author_links" in sql:
            self.scenario.link_sql.append(sql)
            if "FOR SHARE" in sql.upper():
                self.events.append("lock")
            if self.scenario.link_lookup_error:
                raise RuntimeError("synthetic link lookup failure")
            user = self.scenario.current_link_user
            row = SimpleNamespace(user_id=user) if user is not None else None
            result.first = lambda: row
            return result
        if "workspace_members" in sql:
            if self.scenario.memory_lookup_error:
                raise RuntimeError("synthetic membership lookup failure")
            row = SimpleNamespace(ok=1) if self.scenario.member_active else None
            result.first = lambda: row
            return result
        if "embedding <=>" in sql:
            self.events.append("plan:candidates")
            excluded = set(params.get("excluded") or [])
            rows = [c for c in self.scenario.candidates if c[0] not in excluded]
            result.fetchall = lambda: rows
            return result
        if "FROM attributions" in sql:
            self.events.append("apply:contributors")
            wanted = params.get("id")
            if self.scenario.candidates and wanted == self.scenario.candidates[0][0]:
                who = self.scenario.candidate_contributor
            else:
                who = next(
                    (u for m, u in self.state.attributions if str(m) == wanted), None
                )
            result.fetchone = lambda: (str(who),) if who else None
            return result
        if "FROM memory_relations" in sql:
            result.fetchone = lambda: None
            return result
        if sql.startswith("UPDATE memories SET current_version"):
            return result
        if sql.startswith("UPDATE artifact_links SET resolved_user_id"):
            self.events.append("finalize")
            value = params.get("uid")
            for link in self.state.links:
                if str(link["document_id"]) == str(params["doc"]):
                    link["resolved_user_id"] = uuid.UUID(value) if value else None
            return result
        if sql.startswith("UPDATE documents") and "attribution" in sql:
            self.state.final = json.loads(params["final"])
            return result
        if "FROM documents" in sql:
            self.doc.ingestion_status = self.state.status
            result.scalar_one_or_none = lambda: self.doc
            return result
        raise AssertionError(f"unexpected SQL in fake session: {sql[:120]}")


def _install(monkeypatch, session: FakeSession, memories_per_doc: int = 2):
    import sqlalchemy.ext.asyncio as sa_async
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    from sourcemind.core import database, dependencies, redis_client
    from sourcemind.services.attribution import engine as attribution_engine
    from sourcemind.services.ingestion import chunker, embedder, extractor, fact_extractor
    from sourcemind.services.memory import store

    monkeypatch.setattr(
        sa_async, "create_async_engine", lambda *a, **k: SimpleNamespace(dispose=AsyncMock())
    )
    monkeypatch.setattr(sa_async, "async_sessionmaker", lambda **_k: (lambda: session))
    monkeypatch.setattr(redis_client, "init_redis", AsyncMock())
    monkeypatch.setattr(redis_client, "close_redis", AsyncMock())
    monkeypatch.setattr(database, "set_rls_user_context", AsyncMock())
    monkeypatch.setattr(
        dependencies, "require_workspace_permission", AsyncMock(return_value="member")
    )
    from sourcemind.services.conflict import severity
    from sourcemind.services.memory import importance

    def provider(kind: str) -> None:
        # A provider call after the link lock is the defect under test.
        if "lock" in session.events:
            session.events.append(f"provider-after-lock:{kind}")
            raise AssertionError(f"provider call ({kind}) while holding the link lock")
        session.events.append(f"provider:{kind}")

    async def classify(**_k):
        provider("classify")
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(session.scenario.verdict))])

    anthropic_stub = SimpleNamespace(messages=SimpleNamespace(create=classify))
    monkeypatch.setattr(AsyncOpenAI, "__new__", lambda cls, **_k: object())
    monkeypatch.setattr(AsyncAnthropic, "__new__", lambda cls, **_k: anthropic_stub)

    async def rescore_importance(*_a, **_k):
        session.events.append("apply:rescore")
        if session.scenario.relation_error:
            raise RuntimeError("synthetic relation failure after attribution")

    monkeypatch.setattr(importance, "recompute_importance", rescore_importance)
    monkeypatch.setattr(severity, "recompute_severity_for_memory", AsyncMock())

    async def extract(**_k):
        return SimpleNamespace(content="text", content_type="text")

    async def chunk(_e):
        return [SimpleNamespace(content="text")]

    async def facts(*_a, **_k):
        provider("facts")
        return SimpleNamespace(
            facts=[f"fact {i}" for i in range(memories_per_doc)],
            total_chunks=1,
            failed_chunks=0,
            failure_reasons=[],
            wholly_failed=False,
        )

    async def embed(items):
        provider("embed")
        return [SimpleNamespace(content=f, embedding=[0.0], token_count=1) for f in items]

    async def store_memories(_s, ws, doc_id, results, _meta):
        session.events.append("store")
        return [
            SimpleNamespace(
                id=uuid.uuid4(), content=r.content, embedding=r.embedding, workspace_id=ws
            )
            for r in results
        ]

    async def backfill(_s, doc_id, memories):
        anchor = next(
            (
                link
                for link in session.state.links
                if link["document_id"] == doc_id and link["memory_id"] is None
            ),
            None,
        )
        session.events.append("backfill")
        if anchor is None or not memories:
            return 0
        anchor["memory_id"] = memories[0].id
        for memory in memories[1:]:
            session.state.links.append({**anchor, "memory_id": memory.id})
        return len(memories)

    async def update_status(_s, _doc_id, status, **_k):
        if status in (IngestionStatus.COMPLETED, IngestionStatus.FAILED):
            session.events.append(f"status:{status}")
            session.state.status = status

    async def create_attribution(_s, memory_id, user_id, *_a, **_k):
        session.events.append("attribute")
        session.state.attributions.append((memory_id, user_id))

    monkeypatch.setattr(extractor, "extract", extract)
    monkeypatch.setattr(chunker, "chunk", chunk)
    monkeypatch.setattr(
        fact_extractor, "FactExtractor", lambda _c: SimpleNamespace(extract=facts)
    )
    monkeypatch.setattr(embedder, "EmbeddingService", lambda _c: SimpleNamespace(embed=embed))
    monkeypatch.setattr(store, "store_memories", store_memories)
    monkeypatch.setattr(store, "backfill_artifact_links", backfill)
    monkeypatch.setattr(store, "update_document_status", update_status)
    monkeypatch.setattr(attribution_engine, "create_initial_attribution", create_attribution)


def _setup(
    monkeypatch,
    *,
    attribution: dict[str, Any] | None,
    scenario: Scenario,
    with_link: bool = True,
):
    doc_id, ws_id, initiator = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    pipeline_data: dict[str, Any] = {"raw_content": "x", "tags": [], "category": None}
    if attribution is not None:
        pipeline_data["attribution"] = attribution
    doc = SimpleNamespace(
        id=doc_id,
        workspace_id=ws_id,
        submitter_id=initiator,
        ingestion_status=IngestionStatus.PENDING,
        pipeline_data=pipeline_data,
        source_url=None,
        source_type="text",
        memory_count=0,
        error_message=None,
    )
    state = DbState()
    if with_link:
        state.links.append(
            {"document_id": doc_id, "memory_id": None, "resolved_user_id": None}
        )
    session = FakeSession(doc, state, scenario)
    _install(monkeypatch, session)
    return session, doc_id, ws_id, initiator


async def _run(session, doc_id, ws_id, initiator, task=None):
    from sourcemind.workers.ingestion import _run_pipeline

    session.events.clear()
    return await _run_pipeline(task or FakeTask(), str(doc_id), str(ws_id), str(initiator))


def _external(github_user_id: int | None, link_user: uuid.UUID | None) -> dict[str, Any]:
    return {
        "mode": "external",
        "source_tool": "github",
        "github_user_id": github_user_id,
        "source_author": "alice",
        "link_user_id": str(link_user) if link_user else None,
        "resolution": "linked" if link_user else "unlinked",
    }


def _assert_unresolved(session: FakeSession, initiator: uuid.UUID, reason: str) -> None:
    state = session.state
    assert state.attributions == [], "an unresolved GitHub author must get no attribution row"
    assert all(user != initiator for _m, user in state.attributions)
    assert state.links and all(link["resolved_user_id"] is None for link in state.links)
    assert state.final == {"status": "unresolved", "reason": reason}


def _assert_credited(session: FakeSession, user: uuid.UUID) -> None:
    state = session.state
    assert state.attributions, "a linked author must be credited"
    assert {credited for _m, credited in state.attributions} == {user}
    # Invariant: resolved_user_id IS NOT NULL <=> creation attribution credits it.
    assert len(state.links) == 2  # anchor + one clone
    assert all(link["memory_id"] is not None for link in state.links)
    assert all(link["resolved_user_id"] == user for link in state.links)
    assert state.final == {"status": "credited", "user_id": str(user)}


# ── credited ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_linked_author_is_credited_never_the_initiator(monkeypatch) -> None:
    author = uuid.uuid4()
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch,
        attribution=_external(101, author),
        scenario=Scenario(current_link_user=author),
    )

    result = await _run(session, doc_id, ws_id, initiator)

    assert result["status"] == "completed"
    assert all(user != initiator for _m, user in session.state.attributions)
    _assert_credited(session, author)
    assert any("FOR SHARE" in sql.upper() for sql in session.scenario.link_sql)


@pytest.mark.unit
async def test_two_different_linked_authors_are_credited_separately(monkeypatch) -> None:
    alice, bob = uuid.uuid4(), uuid.uuid4()
    session_a, doc_a, ws, initiator = _setup(
        monkeypatch, attribution=_external(1, alice), scenario=Scenario(current_link_user=alice)
    )
    await _run(session_a, doc_a, ws, initiator)
    session_b, doc_b, ws_b, initiator_b = _setup(
        monkeypatch, attribution=_external(2, bob), scenario=Scenario(current_link_user=bob)
    )
    await _run(session_b, doc_b, ws_b, initiator_b)

    _assert_credited(session_a, alice)
    _assert_credited(session_b, bob)


# ── unresolved ───────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_unlinked_author_is_unresolved(monkeypatch) -> None:
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch,
        attribution=_external(101, None),
        scenario=Scenario(current_link_user=uuid.uuid4()),  # linked LATER: not followed
    )

    await _run(session, doc_id, ws_id, initiator)

    _assert_unresolved(session, initiator, "unlinked_at_sync")


@pytest.mark.unit
async def test_author_without_github_account_is_unresolved(monkeypatch) -> None:
    attribution = _external(None, None)
    attribution["resolution"] = "no_github_account"
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch, attribution=attribution, scenario=Scenario()
    )

    await _run(session, doc_id, ws_id, initiator)

    _assert_unresolved(session, initiator, "no_github_account")
    assert session.scenario.link_sql == []


@pytest.mark.unit
async def test_link_deleted_after_queueing_is_unresolved(monkeypatch) -> None:
    author = uuid.uuid4()
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch,
        attribution=_external(101, author),
        scenario=Scenario(current_link_user=None),
    )

    await _run(session, doc_id, ws_id, initiator)

    _assert_unresolved(session, initiator, "link_removed")


@pytest.mark.unit
async def test_link_corrected_to_someone_else_is_not_followed(monkeypatch) -> None:
    author, corrected_to = uuid.uuid4(), uuid.uuid4()
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch,
        attribution=_external(101, author),
        scenario=Scenario(current_link_user=corrected_to),
    )

    await _run(session, doc_id, ws_id, initiator)

    _assert_unresolved(session, initiator, "link_changed")
    assert all(user != corrected_to for _m, user in session.state.attributions)


@pytest.mark.unit
async def test_departed_member_is_unresolved(monkeypatch) -> None:
    author = uuid.uuid4()
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch,
        attribution=_external(101, author),
        scenario=Scenario(current_link_user=author, member_active=False),
    )

    await _run(session, doc_id, ws_id, initiator)

    _assert_unresolved(session, initiator, "member_inactive")


@pytest.mark.unit
@pytest.mark.parametrize("failing", ["link", "membership"])
async def test_lookup_error_fails_closed_to_unresolved(monkeypatch, failing: str) -> None:
    author = uuid.uuid4()
    scenario = Scenario(
        current_link_user=author,
        link_lookup_error=failing == "link",
        memory_lookup_error=failing == "membership",
    )
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch, attribution=_external(101, author), scenario=scenario
    )

    result = await _run(session, doc_id, ws_id, initiator)

    assert result["status"] == "completed"
    _assert_unresolved(session, initiator, "lookup_failed")


# ── failure, retry, idempotency ─────────────────────────────────────────────


@pytest.mark.unit
async def test_failed_attempt_leaves_links_unresolved_and_no_rows(monkeypatch) -> None:
    author = uuid.uuid4()
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch,
        attribution=_external(101, author),
        scenario=Scenario(current_link_user=author, relation_error=True),
    )

    with pytest.raises(RetryScheduled):
        await _run(session, doc_id, ws_id, initiator)

    assert session.state.attributions == []
    assert all(link["resolved_user_id"] is None for link in session.state.links)
    assert session.state.final is None

    # The retry succeeds and finalizes exactly once.
    session.scenario.relation_error = False
    result = await _run(session, doc_id, ws_id, initiator, task=FakeTask(retries=1))
    assert result["status"] == "completed"
    _assert_credited(session, author)


@pytest.mark.unit
async def test_redelivery_after_completion_leaves_identical_state(monkeypatch) -> None:
    author = uuid.uuid4()
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch,
        attribution=_external(101, author),
        scenario=Scenario(current_link_user=author),
    )

    await _run(session, doc_id, ws_id, initiator)
    before = copy.deepcopy(session.state)
    second = await _run(session, doc_id, ws_id, initiator)

    assert second["already_completed"] is True
    assert session.state == before
    _assert_credited(session, author)


# ── non-connector documents are unchanged ───────────────────────────────────


@pytest.mark.unit
async def test_direct_upload_still_credits_its_submitter(monkeypatch) -> None:
    session, doc_id, ws_id, submitter = _setup(
        monkeypatch, attribution=None, scenario=Scenario(), with_link=False
    )

    await _run(session, doc_id, ws_id, submitter)

    assert {user for _m, user in session.state.attributions} == {submitter}
    assert session.scenario.link_sql == []
    assert session.state.final is None


@pytest.mark.unit
@pytest.mark.parametrize("record", [{}, {"mode": "unexpected"}, "garbage"])
async def test_malformed_attribution_record_fails_closed_not_to_submitter(
    monkeypatch, record
) -> None:
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch, attribution=record, scenario=Scenario(current_link_user=uuid.uuid4())
    )

    await _run(session, doc_id, ws_id, initiator)

    _assert_unresolved(session, initiator, "no_github_account")


# ── round 2: provider calls precede the link lock ───────────────────────────


def _conflict_scenario(link_user: uuid.UUID | None) -> Scenario:
    return Scenario(
        current_link_user=link_user,
        candidates=[(str(uuid.uuid4()), "Rival claim from another member.", 0.05)],
        candidate_contributor=uuid.uuid4(),
        verdict={
            "relation": "extends",
            "confidence": 0.95,
            "is_conflict": True,
            "conflict_summary": "The two statements disagree.",
        },
    )


@pytest.mark.unit
async def test_every_provider_call_precedes_the_link_lock(monkeypatch) -> None:
    author = uuid.uuid4()
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch, attribution=_external(101, author), scenario=_conflict_scenario(author)
    )

    result = await _run(session, doc_id, ws_id, initiator)

    events = session.events
    assert result["status"] == "completed"
    assert not [e for e in events if e.startswith("provider-after-lock")], events
    lock = events.index("lock")
    providers = [i for i, e in enumerate(events) if e.startswith("provider:")]
    assert {e for e in events if e.startswith("provider:")} == {
        "provider:facts",
        "provider:embed",
        "provider:classify",
    }
    assert max(providers) < lock
    # The DB-only tail, in order, after the lock.
    tail = [
        e
        for e in events[lock:]
        if e in {"attribute", "backfill", "finalize", "apply:contributors", "status:completed"}
    ]
    first = [tail.index(name) for name in
             ("attribute", "backfill", "finalize", "apply:contributors", "status:completed")]
    assert first == sorted(first)
    assert events.index("plan:candidates") < lock


@pytest.mark.unit
async def test_conflict_still_created_for_a_credited_github_memory(monkeypatch) -> None:
    author = uuid.uuid4()
    scenario = _conflict_scenario(author)
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch, attribution=_external(101, author), scenario=scenario
    )

    await _run(session, doc_id, ws_id, initiator)

    _assert_credited(session, author)
    candidate = uuid.UUID(scenario.candidates[0][0])
    conflicts = [w for w in session.state.writes if w[0] == "conflict"]
    assert len(conflicts) == 2  # one per new memory
    assert all(w[1] == candidate for w in conflicts)
    assert len([w for w in session.state.writes if w[0] == "relation"]) == 2


@pytest.mark.unit
async def test_conflict_still_created_for_a_direct_upload(monkeypatch) -> None:
    session, doc_id, ws_id, submitter = _setup(
        monkeypatch, attribution=None, scenario=_conflict_scenario(None), with_link=False
    )

    await _run(session, doc_id, ws_id, submitter)

    assert {u for _m, u in session.state.attributions} == {submitter}
    assert len([w for w in session.state.writes if w[0] == "conflict"]) == 2
    assert not [e for e in session.events if e.startswith("provider-after-lock")]


@pytest.mark.unit
async def test_unresolved_author_tail_has_no_provider_call(monkeypatch) -> None:
    author = uuid.uuid4()
    session, doc_id, ws_id, initiator = _setup(
        monkeypatch,
        attribution=_external(101, author),
        scenario=_conflict_scenario(None),  # link deleted after queueing
    )

    await _run(session, doc_id, ws_id, initiator)

    _assert_unresolved(session, initiator, "link_removed")
    assert not [e for e in session.events if e.startswith("provider-after-lock")]
    # No contributor row for the new memory, so no conflict is raised for it.
    assert not [w for w in session.state.writes if w[0] == "conflict"]
