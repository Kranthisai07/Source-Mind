"""Worker-level ingestion contract against disposable PostgreSQL and Redis."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.api.v1.memories import create_memory, get_ingestion_job
from sourcemind.api.v1.workspaces import revoke_workspace_member
from sourcemind.core.config import get_settings
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.core.redis_client import close_redis, get_redis, init_redis
from sourcemind.models.attribution import Attribution, AttributionEdit
from sourcemind.models.document import Document, IngestionStatus
from sourcemind.models.memory import Memory
from sourcemind.models.workspace import WorkspaceMember, WorkspaceMembershipStatus
from sourcemind.schemas.memory import MemoryCreate
from sourcemind.services.ingestion.chunker import ChunkResult
from sourcemind.services.ingestion.embedder import EmbeddingResult
from sourcemind.services.ingestion.extractor import ExtractionResult as DocumentExtractionResult
from sourcemind.services.ingestion.fact_extractor import ExtractionResult as FactExtractionResult
from tests.integration.test_security_acceptance_real_services import _configure_environment
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)


class RetryScheduled(RuntimeError):
    """Raised by the fake task when Celery would schedule another attempt."""


class FakeTask:
    max_retries = 3

    def __init__(self, retries: int) -> None:
        self.request = SimpleNamespace(retries=retries)
        self.countdowns: list[int] = []

    def retry(self, *, exc: Exception, countdown: int) -> None:
        self.countdowns.append(countdown)
        if self.request.retries >= self.max_retries:
            raise exc
        raise RetryScheduled(f"retry in {countdown}s")


@dataclass
class WorkerHarness:
    transient_failures: int = 0
    extract_calls: int = 0
    attribution_calls: int = 0
    attribution_delegate: Callable[..., Awaitable[Attribution]] | None = None
    pause_first_extract: bool = False
    first_extract_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_first_extract: asyncio.Event = field(default_factory=asyncio.Event)

    async def extract_document(self, **_kwargs: object) -> DocumentExtractionResult:
        self.extract_calls += 1
        if self.pause_first_extract and self.extract_calls == 1:
            self.first_extract_started.set()
            await self.release_first_extract.wait()
        if self.extract_calls <= self.transient_failures:
            raise RuntimeError("synthetic transient pipeline failure")
        return DocumentExtractionResult(
            content="The selected category must survive worker execution.",
            content_type="text",
        )

    async def chunk_document(self, _extraction: object) -> list[ChunkResult]:
        return [
            ChunkResult(
                content="The selected category must survive worker execution.",
                token_count=8,
                chunk_index=0,
                total_chunks=1,
                chunk_type="text",
            )
        ]

    async def extract_facts(self, *_args: object, **_kwargs: object) -> FactExtractionResult:
        return FactExtractionResult(
            facts=["The selected category survives worker execution."],
            total_chunks=1,
            failed_chunks=0,
            failure_reasons=[],
        )

    async def embed(self, facts: list[str]) -> list[EmbeddingResult]:
        return [
            EmbeddingResult(
                content=fact,
                embedding=[0.01] * 3072,
                token_count=8,
                cached=False,
            )
            for fact in facts
        ]

    async def detect_relations(self, *_args: object, **_kwargs: object) -> list[object]:
        return []

    async def create_attribution(self, *args: object, **kwargs: object) -> Attribution:
        self.attribution_calls += 1
        assert self.attribution_delegate is not None
        return await self.attribution_delegate(*args, **kwargs)


def _validated_database_url(value: str) -> str:
    if not value.startswith("postgresql+asyncpg://"):
        value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
    parsed = make_url(value)
    expected_port = int(get_settings_value("INGESTION_WORKER_EXPECTED_DB_PORT"))
    if (
        parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.port != expected_port
        or parsed.username != "sourcemind_test"
        or parsed.database != "sourcemind_security_test"
    ):
        raise RuntimeError("refusing unexpected ingestion-worker database identity")
    return value


def _validated_redis_url(value: str) -> str:
    parsed = urlsplit(value)
    expected_port = int(get_settings_value("INGESTION_WORKER_EXPECTED_REDIS_PORT"))
    if (
        parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port != expected_port
        or parsed.path != "/15"
    ):
        raise RuntimeError("refusing unexpected ingestion-worker Redis identity")
    return value


def get_settings_value(name: str) -> str:
    import os

    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"missing required ingestion-worker test identity: {name}")
    return value


@pytest.fixture(scope="session")
def ingestion_worker_database_url(request: pytest.FixtureRequest) -> str:
    import os

    custom_url = os.getenv("INGESTION_WORKER_DATABASE_URL", "")
    if not custom_url:
        return str(request.getfixturevalue("supabase_url"))
    if os.getenv("INGESTION_WORKER_TEST_ALLOW_DISPOSABLE") != "1":
        raise RuntimeError("ingestion-worker disposable database is not enabled")
    return _validated_database_url(custom_url)


@pytest.fixture(scope="session")
def ingestion_worker_redis_url(request: pytest.FixtureRequest) -> str:
    import os

    custom_url = os.getenv("INGESTION_WORKER_REDIS_URL", "")
    if not custom_url:
        return str(request.getfixturevalue("security_test_redis_url"))
    if os.getenv("INGESTION_WORKER_TEST_ALLOW_DISPOSABLE") != "1":
        raise RuntimeError("ingestion-worker disposable Redis is not enabled")
    return _validated_redis_url(custom_url)


@pytest_asyncio.fixture
async def ingestion_worker_engine(
    ingestion_worker_database_url: str,
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        ingestion_worker_database_url,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def ingestion_worker_environment(
    monkeypatch: pytest.MonkeyPatch,
    ingestion_worker_database_url: str,
    ingestion_worker_redis_url: str,
) -> AsyncIterator[None]:
    _configure_environment(
        monkeypatch,
        ingestion_worker_database_url,
        ingestion_worker_redis_url,
    )
    await init_redis()
    await get_redis().flushdb()
    try:
        yield
    finally:
        await close_redis()
        get_settings.cache_clear()


@pytest.fixture
def worker_harness(monkeypatch: pytest.MonkeyPatch) -> WorkerHarness:
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    from sourcemind.services.attribution import engine as attribution_engine
    from sourcemind.services.attribution import scorer as attribution_scorer
    from sourcemind.services.ingestion import chunker, embedder, extractor, fact_extractor
    from sourcemind.services.memory import relations

    harness = WorkerHarness(
        attribution_delegate=attribution_engine.create_initial_attribution,
    )
    monkeypatch.setattr(
        attribution_scorer,
        "get_scorer",
        lambda: SimpleNamespace(
            compute_scores=lambda _edits: [
                SimpleNamespace(
                    contribution_weight=1.0,
                    char_diff_score=1.0,
                    semantic_score=1.0,
                    temporal_score=1.0,
                    structural_score=0.0,
                    approval_score=0.0,
                )
            ]
        ),
    )
    monkeypatch.setattr(extractor, "extract", harness.extract_document)
    monkeypatch.setattr(chunker, "chunk", harness.chunk_document)
    monkeypatch.setattr(
        fact_extractor,
        "FactExtractor",
        lambda _client: SimpleNamespace(extract=harness.extract_facts),
    )
    monkeypatch.setattr(
        embedder,
        "EmbeddingService",
        lambda _client: SimpleNamespace(embed=harness.embed),
    )
    monkeypatch.setattr(
        relations,
        "RelationDetector",
        lambda _client: SimpleNamespace(detect=harness.detect_relations),
    )
    monkeypatch.setattr(
        attribution_engine,
        "create_initial_attribution",
        harness.create_attribution,
    )
    monkeypatch.setattr(AsyncOpenAI, "__new__", lambda cls, **_kwargs: object())
    monkeypatch.setattr(AsyncAnthropic, "__new__", lambda cls, **_kwargs: object())
    return harness


def _user(data: SecurityTenantData, user_id: uuid.UUID) -> AuthenticatedUser:
    clerk_id = data.clerk_ids[user_id]
    return AuthenticatedUser(
        user_id=user_id,
        clerk_id=clerk_id,
        email=f"{clerk_id}@example.com",
    )


async def _submit(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    data: SecurityTenantData,
    *,
    category: str | None = None,
    user_id: uuid.UUID | None = None,
) -> tuple[str, uuid.UUID]:
    from sourcemind.workers import ingestion as worker

    submitter_id = user_id or data.member_id
    job_id = f"ingestion-worker-{uuid.uuid4()}"
    monkeypatch.setattr(
        worker.process_document,
        "apply_async",
        lambda **_kwargs: SimpleNamespace(id=job_id),
    )
    payload: dict[str, object] = {
        "content": f"Worker contract payload {uuid.uuid4()}",
        "source_type": "text",
    }
    if category is not None:
        payload["category"] = category
    body = MemoryCreate.model_validate(payload)

    await init_redis()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, submitter_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        response = await create_memory(
            body=body,
            db=session,
            current_user=_user(data, submitter_id),
            request_id=str(uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
            workspace_id=data.target_workspace_id,
        )
    return response.data.job_id, response.data.document_id


async def _poll(
    engine: AsyncEngine,
    data: SecurityTenantData,
    job_id: str,
    *,
    user_id: uuid.UUID | None = None,
):
    caller_id = user_id or data.member_id
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, caller_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        response = await get_ingestion_job(
            job_id=job_id,
            db=session,
            current_user=_user(data, caller_id),
            request_id=str(uuid.uuid4()),
        )
    return response.data


async def _document(
    engine: AsyncEngine,
    data: SecurityTenantData,
    document_id: uuid.UUID,
) -> Document:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        document = await session.scalar(select(Document).where(Document.id == document_id))
        assert document is not None
        session.expunge(document)
        return document


async def _memory_count(
    engine: AsyncEngine,
    data: SecurityTenantData,
    document_id: uuid.UUID,
) -> int:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        count = await session.scalar(
            select(func.count(Memory.id)).where(Memory.document_id == document_id)
        )
        return int(count or 0)


async def _stored_output_counts(
    engine: AsyncEngine,
    data: SecurityTenantData,
    document_id: uuid.UUID,
) -> tuple[int, int, int]:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        memory_ids = select(Memory.id).where(Memory.document_id == document_id)
        memory_count = await session.scalar(
            select(func.count(Memory.id)).where(Memory.document_id == document_id)
        )
        attribution_count = await session.scalar(
            select(func.count(Attribution.id)).where(Attribution.memory_id.in_(memory_ids))
        )
        edit_count = await session.scalar(
            select(func.count(AttributionEdit.id)).where(AttributionEdit.memory_id.in_(memory_ids))
        )
        return (
            int(memory_count or 0),
            int(attribution_count or 0),
            int(edit_count or 0),
        )


async def _submit_after_ambiguous_publication(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    data: SecurityTenantData,
) -> tuple[str, uuid.UUID, list[dict[str, object]]]:
    from sourcemind.workers import ingestion as worker

    deliveries: list[dict[str, object]] = []

    def accepted_then_failed(**kwargs: object) -> SimpleNamespace:
        deliveries.append(dict(kwargs))
        if len(deliveries) == 1:
            raise RuntimeError("synthetic exception after broker acceptance")
        return SimpleNamespace(id=kwargs["task_id"])

    monkeypatch.setattr(worker.process_document, "apply_async", accepted_then_failed)
    idempotency_key = str(uuid.uuid4())
    body = MemoryCreate.model_validate(
        {
            "content": f"Ambiguous publication payload {uuid.uuid4()}",
            "source_type": "text",
        }
    )

    async def submit_once():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, data.member_id)
            await set_rls_workspace_context(session, data.target_workspace_id)
            return await create_memory(
                body=body,
                db=session,
                current_user=_user(data, data.member_id),
                request_id=str(uuid.uuid4()),
                idempotency_key=idempotency_key,
                workspace_id=data.target_workspace_id,
            )

    with pytest.raises(RuntimeError, match="broker acceptance"):
        await submit_once()
    response = await submit_once()
    assert len(deliveries) == 2
    assert deliveries[0]["task_id"] == deliveries[1]["task_id"]
    assert deliveries[0]["kwargs"] == deliveries[1]["kwargs"]
    return response.data.job_id, response.data.document_id, deliveries


async def _begin_member_handoff(
    engine: AsyncEngine,
    data: SecurityTenantData,
) -> None:
    from sourcemind.api.v1.team import InitiateHandoffBody, initiate_handoff

    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        await initiate_handoff(
            workspace_id=data.target_workspace_id,
            body=InitiateHandoffBody(departing_user_id=data.member_id),
            db=session,
            current_user=_user(data, data.owner_id),
            request_id=str(uuid.uuid4()),
        )


async def _set_retrying(
    engine: AsyncEngine,
    data: SecurityTenantData,
    document_id: uuid.UUID,
) -> None:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        document = await session.scalar(
            select(Document).where(Document.id == document_id)
        )
        assert document is not None
        pipeline_data = dict(document.pipeline_data or {})
        pipeline_data["current_stage"] = "retrying"
        document.pipeline_data = pipeline_data
        document.ingestion_status = IngestionStatus.PROCESSING
        await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transient_error_is_retrying_then_worker_succeeds(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.workers.ingestion import _run_pipeline

    data = await _seed_security_tenants(ingestion_worker_engine)
    job_id, document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )
    worker_harness.transient_failures = 1
    task = FakeTask(retries=0)

    with pytest.raises(RetryScheduled):
        await _run_pipeline(
            task,
            str(document_id),
            str(data.target_workspace_id),
            str(data.member_id),
        )

    retrying = await _poll(ingestion_worker_engine, data, job_id)
    assert retrying.status == "retrying"
    assert "retry" in retrying.message.lower()
    assert retrying.error is None
    retrying_document = await _document(ingestion_worker_engine, data, document_id)
    assert retrying_document.ingestion_status == IngestionStatus.PROCESSING
    assert retrying_document.pipeline_data["current_stage"] == "retrying"
    assert retrying_document.error_message is None
    assert await _memory_count(ingestion_worker_engine, data, document_id) == 0

    task.request.retries = 1
    result = await _run_pipeline(
        task,
        str(document_id),
        str(data.target_workspace_id),
        str(data.member_id),
    )

    assert result["status"] == "completed"
    completed = await _poll(ingestion_worker_engine, data, job_id)
    assert completed.status == "completed"
    assert completed.error is None
    assert worker_harness.attribution_calls == 1
    assert await _memory_count(ingestion_worker_engine, data, document_id) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exhausted_retries_are_terminal_failed(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.workers.ingestion import _run_pipeline

    data = await _seed_security_tenants(ingestion_worker_engine)
    job_id, document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )
    worker_harness.transient_failures = 10

    with pytest.raises(RuntimeError, match="Ingestion pipeline failed"):
        await _run_pipeline(
            FakeTask(retries=3),
            str(document_id),
            str(data.target_workspace_id),
            str(data.member_id),
        )

    failed = await _poll(ingestion_worker_engine, data, job_id)
    assert failed.status == "failed"
    assert failed.error == "Ingestion failed (RuntimeError)."
    failed_document = await _document(ingestion_worker_engine, data, document_id)
    assert failed_document.ingestion_status == IngestionStatus.FAILED
    assert failed_document.pipeline_data["current_stage"] == "failed"
    assert await _memory_count(ingestion_worker_engine, data, document_id) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_revoked_access_stays_terminal_without_worker_processing(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.workers.ingestion import _run_pipeline

    data = await _seed_security_tenants(ingestion_worker_engine)
    job_id, document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )

    async with AsyncSession(ingestion_worker_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        await revoke_workspace_member(
            workspace_id=data.target_workspace_id,
            user_id=data.member_id,
            db=session,
            current_user=_user(data, data.owner_id),
            idempotency_key=str(uuid.uuid4()),
        )

    result = await _run_pipeline(
        FakeTask(retries=0),
        str(document_id),
        str(data.target_workspace_id),
        str(data.member_id),
    )

    assert result == {"status": "rejected", "error": "Workspace access revoked"}
    failed = await _poll(
        ingestion_worker_engine,
        data,
        job_id,
        user_id=data.owner_id,
    )
    assert failed.status == "failed"
    assert failed.error == "Workspace access revoked before ingestion."
    assert worker_harness.extract_calls == 0
    assert await _memory_count(ingestion_worker_engine, data, document_id) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_selected_category_reaches_memory_through_worker(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.workers.ingestion import _run_pipeline

    data = await _seed_security_tenants(ingestion_worker_engine)
    job_id, document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
        category="incident",
    )

    queued = await _document(ingestion_worker_engine, data, document_id)
    assert queued.pipeline_data["category"] == "incident"

    result = await _run_pipeline(
        FakeTask(retries=0),
        str(document_id),
        str(data.target_workspace_id),
        str(data.member_id),
    )
    assert result["status"] == "completed"

    async with AsyncSession(ingestion_worker_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        memory = await session.scalar(select(Memory).where(Memory.document_id == document_id))
        assert memory is not None
        assert memory.category == "incident"

    completed = await _poll(ingestion_worker_engine, data, job_id)
    assert completed.status == "completed"
    assert worker_harness.attribution_calls == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ambiguous_publication_redelivery_preserves_committed_output(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.workers.ingestion import _run_pipeline

    data = await _seed_security_tenants(ingestion_worker_engine)
    job_id, document_id, deliveries = await _submit_after_ambiguous_publication(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )
    delivery = deliveries[0]["kwargs"]
    assert isinstance(delivery, dict)

    first = await _run_pipeline(FakeTask(retries=0), **delivery)
    counts_after_first = await _stored_output_counts(
        ingestion_worker_engine,
        data,
        document_id,
    )
    second = await _run_pipeline(FakeTask(retries=0), **delivery)
    counts_after_redelivery = await _stored_output_counts(
        ingestion_worker_engine,
        data,
        document_id,
    )

    assert first["status"] == "completed"
    assert second == {
        "status": "completed",
        "document_id": str(document_id),
        "memories_created": 1,
        "already_completed": True,
    }
    assert counts_after_first == (1, 1, 1)
    assert counts_after_redelivery == counts_after_first
    assert worker_harness.extract_calls == 1
    completed = await _poll(ingestion_worker_engine, data, job_id)
    assert completed.status == "completed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_duplicate_deliveries_commit_output_once(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.workers.ingestion import _run_pipeline

    data = await _seed_security_tenants(ingestion_worker_engine)
    _job_id, document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )
    worker_harness.pause_first_extract = True
    delivery = {
        "document_id": str(document_id),
        "workspace_id": str(data.target_workspace_id),
        "user_id": str(data.member_id),
    }

    first_delivery = asyncio.create_task(_run_pipeline(FakeTask(retries=0), **delivery))
    await asyncio.wait_for(worker_harness.first_extract_started.wait(), timeout=5)
    second_delivery = asyncio.create_task(_run_pipeline(FakeTask(retries=0), **delivery))
    await asyncio.sleep(0.25)
    worker_harness.release_first_extract.set()
    first, second = await asyncio.gather(first_delivery, second_delivery)

    assert {first["status"], second["status"]} == {"completed", "in_progress"}
    assert await _stored_output_counts(
        ingestion_worker_engine,
        data,
        document_id,
    ) == (1, 1, 1)
    assert worker_harness.extract_calls == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stale_processing_state_is_recovered_after_worker_crash(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.workers.ingestion import _run_pipeline

    data = await _seed_security_tenants(ingestion_worker_engine)
    _job_id, document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )
    async with AsyncSession(ingestion_worker_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        document = await session.scalar(select(Document).where(Document.id == document_id))
        assert document is not None
        pipeline_data = dict(document.pipeline_data or {})
        pipeline_data["current_stage"] = "extracting"
        document.pipeline_data = pipeline_data
        document.ingestion_status = IngestionStatus.PROCESSING
        await session.commit()

    result = await _run_pipeline(
        FakeTask(retries=0),
        str(document_id),
        str(data.target_workspace_id),
        str(data.member_id),
    )

    assert result["status"] == "completed"
    assert await _stored_output_counts(
        ingestion_worker_engine,
        data,
        document_id,
    ) == (1, 1, 1)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handoff_departure_terminalizes_submitter_ingestion_jobs(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.workers.ingestion import _run_pipeline

    data = await _seed_security_tenants(ingestion_worker_engine)
    queued_job_id, queued_document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )
    _retrying_job_id, retrying_document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )
    _control_job_id, control_document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
        user_id=data.owner_id,
    )
    await _set_retrying(
        ingestion_worker_engine,
        data,
        retrying_document_id,
    )

    await _begin_member_handoff(ingestion_worker_engine, data)

    membership_status: str | None = None
    async with AsyncSession(ingestion_worker_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        membership_status = await session.scalar(
            select(WorkspaceMember.status).where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.member_id,
            )
    )
    assert membership_status == WorkspaceMembershipStatus.DEPARTING

    worker_result = await _run_pipeline(
        FakeTask(retries=0),
        str(queued_document_id),
        str(data.target_workspace_id),
        str(data.member_id),
    )
    assert worker_result == {
        "status": "rejected",
        "error": "Workspace access revoked",
    }
    assert worker_harness.extract_calls == 0
    assert await _stored_output_counts(
        ingestion_worker_engine,
        data,
        queued_document_id,
    ) == (0, 0, 0)
    polled_job = await _poll(
        ingestion_worker_engine,
        data,
        queued_job_id,
        user_id=data.owner_id,
    )

    expected_error = (
        "Workspace access ended before ingestion because the submitter began departure."
    )
    queued = await _document(
        ingestion_worker_engine,
        data,
        queued_document_id,
    )
    retrying = await _document(
        ingestion_worker_engine,
        data,
        retrying_document_id,
    )
    control = await _document(
        ingestion_worker_engine,
        data,
        control_document_id,
    )
    assert queued.ingestion_status == IngestionStatus.FAILED
    assert queued.error_message == expected_error
    assert queued.pipeline_data["current_stage"] == "failed"
    assert retrying.ingestion_status == IngestionStatus.FAILED
    assert retrying.error_message == expected_error
    assert retrying.pipeline_data["current_stage"] == "failed"
    assert control.ingestion_status == IngestionStatus.PENDING
    assert control.pipeline_data["current_stage"] == "queued"
    assert polled_job.status == "failed"
    assert polled_job.error == expected_error


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_handoff_rolls_back_ingestion_terminalization(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sourcemind.api.v1.team import InitiateHandoffBody, initiate_handoff
    from sourcemind.services.attribution import handoff as handoff_service

    data = await _seed_security_tenants(ingestion_worker_engine)
    _job_id, document_id = await _submit(
        ingestion_worker_engine,
        monkeypatch,
        data,
    )
    observed_before_failure: list[str] = []
    create_handoff_record = handoff_service.create_handoff_record

    async def fail_after_handoff_changes(*args: object, **kwargs: object) -> uuid.UUID:
        await create_handoff_record(*args, **kwargs)
        session = kwargs["session"]
        assert isinstance(session, AsyncSession)
        observed_status = await session.scalar(
            select(Document.ingestion_status).where(Document.id == document_id)
        )
        assert observed_status is not None
        observed_before_failure.append(str(observed_status))
        raise RuntimeError("synthetic handoff transaction failure")

    monkeypatch.setattr(
        handoff_service,
        "create_handoff_record",
        fail_after_handoff_changes,
    )
    async with AsyncSession(ingestion_worker_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        with pytest.raises(RuntimeError, match="synthetic handoff transaction failure"):
            await initiate_handoff(
                workspace_id=data.target_workspace_id,
                body=InitiateHandoffBody(departing_user_id=data.member_id),
                db=session,
                current_user=_user(data, data.owner_id),
                request_id=str(uuid.uuid4()),
            )
        await session.rollback()

    assert observed_before_failure == [IngestionStatus.FAILED]
    document = await _document(ingestion_worker_engine, data, document_id)
    assert document.ingestion_status == IngestionStatus.PENDING
    assert document.error_message is None
    assert document.pipeline_data["current_stage"] == "queued"
    async with AsyncSession(ingestion_worker_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        membership_status = await session.scalar(
            select(WorkspaceMember.status).where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.member_id,
            )
        )
        handoff_count = await session.scalar(
            text(
                "SELECT count(*) FROM handoff_records "
                "WHERE workspace_id = CAST(:workspace_id AS uuid)"
            ),
            {"workspace_id": str(data.target_workspace_id)},
        )
    assert membership_status == WorkspaceMembershipStatus.ACTIVE
    assert int(handoff_count or 0) == 0
