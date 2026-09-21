"""Focused real-service regressions for the backend review findings."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.core.config import get_settings
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.exceptions import IdempotencyConflictError, ServiceUnavailableError
from sourcemind.models.document import Document
from tests.integration.test_security_acceptance_real_services import (
    _auth,
    _signing_material,
    _token,
)
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)


class _SecretURL(str):
    def __repr__(self) -> str:
        parsed = make_url(str(self))
        return repr(parsed.render_as_string(hide_password=True))


@pytest.fixture(scope="session")
def backend_review_database_url(request: pytest.FixtureRequest) -> str:
    custom_url = os.getenv("BACKEND_REVIEW_DATABASE_URL", "")
    if not custom_url:
        return str(request.getfixturevalue("supabase_url"))
    if os.getenv("BACKEND_REVIEW_TEST_ALLOW_DISPOSABLE") != "1":
        raise RuntimeError("backend-review database target is not enabled")
    expected_port = int(os.environ["BACKEND_REVIEW_EXPECTED_DATABASE_PORT"])
    expected_database = os.environ["BACKEND_REVIEW_EXPECTED_DATABASE_NAME"]
    if not custom_url.startswith("postgresql+asyncpg://"):
        custom_url = custom_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    parsed = make_url(custom_url)
    if (
        parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.port != expected_port
        or parsed.username != "sourcemind_test"
        or parsed.database != expected_database
    ):
        raise RuntimeError("refusing unexpected backend-review database identity")
    return _SecretURL(custom_url)


@pytest.fixture(scope="session")
def backend_review_redis_url(request: pytest.FixtureRequest) -> str:
    custom_url = os.getenv("BACKEND_REVIEW_REDIS_URL", "")
    if not custom_url:
        return str(request.getfixturevalue("security_test_redis_url"))
    if os.getenv("BACKEND_REVIEW_TEST_ALLOW_DISPOSABLE") != "1":
        raise RuntimeError("backend-review Redis target is not enabled")
    expected = (
        f"redis://127.0.0.1:{os.environ['BACKEND_REVIEW_EXPECTED_REDIS_PORT']}/15"
    )
    if custom_url != expected:
        raise RuntimeError("refusing unexpected backend-review Redis identity")
    return custom_url


@pytest_asyncio.fixture
async def backend_review_engine(
    backend_review_database_url: str,
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        backend_review_database_url,
        pool_size=6,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


def _configure_environment(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    redis_url: str,
) -> None:
    publishable_host = base64.b64encode(b"clerk.security.test$").decode()
    values = {
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "ENVIRONMENT": "development",
        "DEBUG": "false",
        "AUTH_DEV_BYPASS_ENABLED": "false",
        "CLERK_SECRET_KEY": "sk_test_backend_review",
        "CLERK_PUBLISHABLE_KEY": f"pk_test_{publishable_host}",
        "CLERK_AUTHORIZED_PARTIES": '["https://app.security.test"]',
        "CORS_ORIGINS": '["https://app.security.test"]',
        "OPENAI_API_KEY": "test-openai-not-sent",
        "ANTHROPIC_API_KEY": "test-anthropic-not-sent",
        "URL_INGESTION_ENABLED": "false",
        "SLACK_MEMORY_COMMANDS_ENABLED": "false",
        "FF_NEO4J_ATTRIBUTION": "false",
        "FF_KAFKA_EVENTS": "false",
        "RATE_LIMIT_FAIL_CLOSED": "true",
        "RATE_LIMIT_INGESTION_PER_MINUTE": "100",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


class _TwoCallerBarrier:
    def __init__(self) -> None:
        self._count = 0
        self._lock = asyncio.Lock()
        self._ready = asyncio.Event()

    async def wait(self) -> None:
        async with self._lock:
            self._count += 1
            if self._count == 2:
                self._ready.set()
        await self._ready.wait()


class _RaceRedis:
    """Synchronize the old GET path without obstructing an atomic reservation."""

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self._legacy_get_barrier = _TwoCallerBarrier()
        self.atomic_reservation_seen = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def eval(self, *args: object, **kwargs: object) -> object:
        self.atomic_reservation_seen = True
        return await self._delegate.eval(*args, **kwargs)  # type: ignore[attr-defined]

    async def get(self, key: str) -> object:
        value = await self._delegate.get(key)  # type: ignore[attr-defined]
        if not self.atomic_reservation_seen:
            await self._legacy_get_barrier.wait()
        return value


class _RaceSession:
    def __init__(
        self,
        delegate: AsyncSession,
        redis: _RaceRedis,
        legacy_hash_barrier: _TwoCallerBarrier,
    ) -> None:
        self._delegate = delegate
        self._redis = redis
        self._legacy_hash_barrier = legacy_hash_barrier

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    async def execute(
        self,
        statement: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        result = await self._delegate.execute(statement, *args, **kwargs)
        rendered = str(statement)
        if (
            not self._redis.atomic_reservation_seen
            and "FROM documents" in rendered
            and "documents.sha256_hash" in rendered
        ):
            await self._legacy_hash_barrier.wait()
        return result


class _DispatchSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> SimpleNamespace:
        task_id = f"review-job-{len(self.calls) + 1}"
        self.calls.append({"task_id": task_id, **kwargs})
        return SimpleNamespace(id=task_id)


class _FailFirstDispatch:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("synthetic publication failure")
        task_id = str(kwargs.get("task_id") or f"recovery-job-{len(self.calls)}")
        return SimpleNamespace(id=task_id)


@asynccontextmanager
async def _publication_recovery_client(
    engine: AsyncEngine,
    database_url: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, SecurityTenantData, dict[str, str]]]:
    _configure_environment(monkeypatch, database_url, redis_url)
    data = await _seed_security_tenants(engine)
    private_pem, public_jwk = _signing_material()
    clerk_id = data.clerk_ids[data.member_id]

    async def fetch_profile(_clerk_id: str, _secret: str) -> tuple[str, str]:
        return f"{clerk_id}@example.com", clerk_id

    from sourcemind.core import dependencies
    from sourcemind.core.redis_client import get_redis
    from sourcemind.main import create_app

    dependencies._jwks_cache.clear()
    dependencies._user_profile_cache.clear()
    app = create_app()
    with (
        patch(
            "sourcemind.core.dependencies._fetch_jwks",
            return_value=[public_jwk],
        ),
        patch(
            "sourcemind.core.dependencies._fetch_clerk_user_profile",
            side_effect=fetch_profile,
        ),
    ):
        async with app.router.lifespan_context(app):
            await get_redis().flushdb()
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://backend-review.test",
            ) as client:
                yield client, data, _auth(_token(private_pem, clerk_id))


async def _publication_document(
    engine: AsyncEngine,
    data: SecurityTenantData,
    content: str,
) -> Document:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.member_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        document = await session.scalar(
            select(Document).where(
                Document.workspace_id == data.target_workspace_id,
                Document.sha256_hash == hashlib.sha256(content.encode()).hexdigest(),
            )
        )
        assert document is not None
        session.expunge(document)
        return document


async def _run_concurrent_submissions(
    engine: AsyncEngine,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
    contents: tuple[str, str],
) -> tuple[list[object], list[Document], _DispatchSpy]:
    _configure_environment(monkeypatch, str(engine.url), redis_url)
    from sourcemind.core.redis_client import close_redis, get_redis, init_redis
    from sourcemind.services.ingestion import receiver
    from sourcemind.workers.ingestion import process_document

    await init_redis()
    real_redis = get_redis()
    race_redis = _RaceRedis(real_redis)
    data = await _seed_security_tenants(engine)
    idempotency_key = str(uuid.uuid4())
    dispatch = _DispatchSpy()
    start = _TwoCallerBarrier()
    legacy_hash_barrier = _TwoCallerBarrier()

    async def submit(content: str) -> object:
        async with AsyncSession(engine, expire_on_commit=False) as raw_session:
            await set_rls_user_context(raw_session, data.member_id)
            await set_rls_workspace_context(raw_session, data.target_workspace_id)
            await start.wait()
            try:
                return await receiver.receive(
                    session=_RaceSession(
                        raw_session,
                        race_redis,
                        legacy_hash_barrier,
                    ),  # type: ignore[arg-type]
                    workspace_id=data.target_workspace_id,
                    user_id=data.member_id,
                    content=content,
                    source_type="text",
                    idempotency_key=idempotency_key,
                )
            except Exception as exc:
                await raw_session.rollback()
                return exc

    try:
        with (
            patch.object(receiver, "get_redis", return_value=race_redis),
            patch.object(process_document, "apply_async", side_effect=dispatch),
        ):
            results = list(await asyncio.gather(*(submit(value) for value in contents)))

        async with AsyncSession(engine) as query_session:
            await set_rls_user_context(query_session, data.member_id)
            await set_rls_workspace_context(query_session, data.target_workspace_id)
            documents = list(
                (
                    await query_session.scalars(
                        select(Document).where(
                            Document.workspace_id == data.target_workspace_id,
                            Document.pipeline_data["idempotency_key"].astext
                            == idempotency_key,
                        )
                    )
                ).all()
            )
            for document in documents:
                query_session.expunge(document)
        return results, documents, dispatch
    finally:
        await close_redis()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_identical_concurrent_submissions_share_one_result(
    backend_review_engine: AsyncEngine,
    backend_review_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, documents, dispatch = await _run_concurrent_submissions(
        backend_review_engine,
        backend_review_redis_url,
        monkeypatch,
        ("identical payload", "identical payload"),
    )

    assert all(isinstance(result, dict) for result in results)
    assert results[0] == results[1]
    assert len(documents) == 1
    assert len(dispatch.calls) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_key_with_different_payload_conflicts_atomically(
    backend_review_engine: AsyncEngine,
    backend_review_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results, documents, dispatch = await _run_concurrent_submissions(
        backend_review_engine,
        backend_review_redis_url,
        monkeypatch,
        ("first payload", "different payload"),
    )

    responses = [result for result in results if isinstance(result, dict)]
    conflicts = [
        result for result in results if isinstance(result, IdempotencyConflictError)
    ]
    assert len(responses) == 1
    assert len(conflicts) == 1
    assert conflicts[0].http_status == 409
    assert conflicts[0].code.value == "SM033"
    assert len(documents) == 1
    assert len(dispatch.calls) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_request_releases_reservation_for_retry(
    backend_review_engine: AsyncEngine,
    backend_review_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(
        monkeypatch,
        str(backend_review_engine.url),
        backend_review_redis_url,
    )
    from sourcemind.core.redis_client import close_redis, get_redis, init_redis
    from sourcemind.services.ingestion import receiver
    from sourcemind.workers.ingestion import process_document

    await init_redis()
    redis = get_redis()
    data = await _seed_security_tenants(backend_review_engine)
    idempotency_key = str(uuid.uuid4())
    cache_key = receiver._idempotency_cache_key(
        data.target_workspace_id,
        data.member_id,
        idempotency_key,
    )
    dispatch = _DispatchSpy()
    try:
        async with AsyncSession(backend_review_engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, data.member_id)
            await set_rls_workspace_context(session, data.target_workspace_id)
            with patch.object(
                receiver,
                "_receive_reserved",
                side_effect=RuntimeError("synthetic pre-persistence failure"),
            ):
                with pytest.raises(RuntimeError, match="synthetic"):
                    await receiver.receive(
                        session=session,
                        workspace_id=data.target_workspace_id,
                        user_id=data.member_id,
                        content="retry after a handled failure",
                        idempotency_key=idempotency_key,
                    )

        assert await redis.exists(cache_key) == 0

        async with AsyncSession(backend_review_engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, data.member_id)
            await set_rls_workspace_context(session, data.target_workspace_id)
            with patch.object(process_document, "apply_async", side_effect=dispatch):
                response = await receiver.receive(
                    session=session,
                    workspace_id=data.target_workspace_id,
                    user_id=data.member_id,
                    content="retry after a handled failure",
                    idempotency_key=idempotency_key,
                )

        assert response["already_exists"] is False
        assert len(dispatch.calls) == 1
    finally:
        await redis.delete(cache_key)
        await close_redis()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_in_progress_wait_is_bounded_and_expired_lease_recovers(
    backend_review_engine: AsyncEngine,
    backend_review_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(
        monkeypatch,
        str(backend_review_engine.url),
        backend_review_redis_url,
    )
    from sourcemind.core.redis_client import close_redis, get_redis, init_redis
    from sourcemind.services.ingestion import receiver
    from sourcemind.workers.ingestion import process_document

    await init_redis()
    redis = get_redis()
    data = await _seed_security_tenants(backend_review_engine)
    idempotency_key = str(uuid.uuid4())
    content = "retry after an abandoned reservation"
    cache_key = receiver._idempotency_cache_key(
        data.target_workspace_id,
        data.member_id,
        idempotency_key,
    )
    fingerprint = receiver._request_fingerprint(
        content=content,
        url=None,
        source_type="text",
        title=None,
        tags=None,
        category=None,
    )
    await redis.hset(
        cache_key,
        mapping={
            "state": "in_progress",
            "fingerprint": fingerprint,
            "token": "abandoned-owner",
        },
    )
    await redis.pexpire(cache_key, 500)
    dispatch = _DispatchSpy()
    try:
        async with AsyncSession(backend_review_engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, data.member_id)
            await set_rls_workspace_context(session, data.target_workspace_id)
            with patch.object(receiver, "_IDEM_WAIT_SECONDS", 0.01):
                with pytest.raises(ServiceUnavailableError) as raised:
                    await receiver.receive(
                        session=session,
                        workspace_id=data.target_workspace_id,
                        user_id=data.member_id,
                        content=content,
                        idempotency_key=idempotency_key,
                    )
        assert raised.value.details["retry_after_seconds"] >= 1
        assert not dispatch.calls

        await asyncio.sleep(0.55)
        async with AsyncSession(backend_review_engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, data.member_id)
            await set_rls_workspace_context(session, data.target_workspace_id)
            with patch.object(process_document, "apply_async", side_effect=dispatch):
                response = await receiver.receive(
                    session=session,
                    workspace_id=data.target_workspace_id,
                    user_id=data.member_id,
                    content=content,
                    idempotency_key=idempotency_key,
                )

        assert response["already_exists"] is False
        assert len(dispatch.calls) == 1
    finally:
        await redis.delete(cache_key)
        await close_redis()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_publication_failure_retry_recovers_pending_document(
    backend_review_engine: AsyncEngine,
    backend_review_database_url: str,
    backend_review_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sourcemind.workers.ingestion import process_document

    content = f"publication recovery {uuid.uuid4()}"
    idempotency_key = str(uuid.uuid4())
    dispatch = _FailFirstDispatch()

    async with _publication_recovery_client(
        backend_review_engine,
        backend_review_database_url,
        backend_review_redis_url,
        monkeypatch,
    ) as (client, data, auth_headers):
        headers = {**auth_headers, "Idempotency-Key": idempotency_key}
        with patch.object(process_document, "apply_async", side_effect=dispatch):
            failed = await client.post(
                f"/v1/memories?workspace_id={data.target_workspace_id}",
                headers=headers,
                json={"content": content, "source_type": "text"},
            )
            assert failed.status_code == 500

            ambiguous = await _publication_document(
                backend_review_engine,
                data,
                content,
            )
            assert ambiguous.ingestion_status == "pending"
            assert ambiguous.ingestion_job_id is not None
            assert ambiguous.pipeline_data["dispatch_state"] == "uncertain"
            assert ambiguous.pipeline_data["dispatch_attempts"] == 1

            recovered = await client.post(
                f"/v1/memories?workspace_id={data.target_workspace_id}",
                headers=headers,
                json={"content": content, "source_type": "text"},
            )
            already_queued = await client.post(
                f"/v1/memories?workspace_id={data.target_workspace_id}",
                headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"content": content, "source_type": "text"},
            )

        assert recovered.status_code == 202, recovered.text
        assert already_queued.status_code == 202, already_queued.text
        response = recovered.json()["data"]
        assert response["job_id"] == ambiguous.ingestion_job_id
        assert response["document_id"] == str(ambiguous.id)
        assert already_queued.json()["data"]["job_id"] == response["job_id"]
        assert len(dispatch.calls) == 2
        assert dispatch.calls[0]["task_id"] == dispatch.calls[1]["task_id"]

        queued = await _publication_document(backend_review_engine, data, content)
        assert queued.pipeline_data["dispatch_state"] == "queued"
        assert queued.pipeline_data["dispatch_attempts"] == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_recovery_attempts_publish_once(
    backend_review_engine: AsyncEngine,
    backend_review_database_url: str,
    backend_review_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sourcemind.workers.ingestion import process_document

    content = f"concurrent publication recovery {uuid.uuid4()}"
    original_key = str(uuid.uuid4())
    dispatch = _FailFirstDispatch()

    async with _publication_recovery_client(
        backend_review_engine,
        backend_review_database_url,
        backend_review_redis_url,
        monkeypatch,
    ) as (client, data, auth_headers):
        with patch.object(process_document, "apply_async", side_effect=dispatch):
            failed = await client.post(
                f"/v1/memories?workspace_id={data.target_workspace_id}",
                headers={**auth_headers, "Idempotency-Key": original_key},
                json={"content": content, "source_type": "text"},
            )
            assert failed.status_code == 500

            async def retry(idempotency_key: str) -> httpx.Response:
                return await client.post(
                    f"/v1/memories?workspace_id={data.target_workspace_id}",
                    headers={**auth_headers, "Idempotency-Key": idempotency_key},
                    json={"content": content, "source_type": "text"},
                )

            responses = await asyncio.gather(
                retry(original_key),
                retry(str(uuid.uuid4())),
            )

        assert [response.status_code for response in responses] == [202, 202]
        returned = [response.json()["data"] for response in responses]
        assert len({item["document_id"] for item in returned}) == 1
        assert len({item["job_id"] for item in returned}) == 1
        assert len(dispatch.calls) == 2

        queued = await _publication_document(backend_review_engine, data, content)
        assert queued.pipeline_data["dispatch_state"] == "queued"
        assert queued.pipeline_data["dispatch_attempts"] == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recovered_job_id_is_pollable_through_real_route(
    backend_review_engine: AsyncEngine,
    backend_review_database_url: str,
    backend_review_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sourcemind.workers.ingestion import process_document

    content = f"pollable publication recovery {uuid.uuid4()}"
    idempotency_key = str(uuid.uuid4())
    dispatch = _FailFirstDispatch()

    async with _publication_recovery_client(
        backend_review_engine,
        backend_review_database_url,
        backend_review_redis_url,
        monkeypatch,
    ) as (client, data, auth_headers):
        headers = {**auth_headers, "Idempotency-Key": idempotency_key}
        with patch.object(process_document, "apply_async", side_effect=dispatch):
            failed = await client.post(
                f"/v1/memories?workspace_id={data.target_workspace_id}",
                headers=headers,
                json={"content": content, "source_type": "text"},
            )
            assert failed.status_code == 500
            recovered = await client.post(
                f"/v1/memories?workspace_id={data.target_workspace_id}",
                headers=headers,
                json={"content": content, "source_type": "text"},
            )

        assert recovered.status_code == 202, recovered.text
        recovered_data = recovered.json()["data"]
        polled = await client.get(
            f"/v1/memories/jobs/{recovered_data['job_id']}",
            headers=auth_headers,
        )

        assert polled.status_code == 200, polled.text
        polled_data = polled.json()["data"]
        assert polled_data["job_id"] == recovered_data["job_id"]
        assert polled_data["document_id"] == recovered_data["document_id"]
        assert polled_data["status"] == "queued"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_redis_429_exposes_positive_retry_after(
    backend_review_database_url: str,
    backend_review_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(
        monkeypatch,
        backend_review_database_url,
        backend_review_redis_url,
    )
    from sourcemind.core import rate_limit
    from sourcemind.core.redis_client import close_redis, get_redis, init_redis
    from sourcemind.main import create_app

    await init_redis()
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    key = f"rate-limit:search:{workspace_id}:{user_id}"
    redis = get_redis()
    await redis.delete(key)
    app = create_app()

    @app.get("/__test/rate-limit")
    async def limited_route() -> dict[str, bool]:
        await rate_limit.enforce_rate_limit(
            rate_limit.RateLimitedOperation.SEARCH,
            user_id,
            workspace_id,
        )
        return {"ok": True}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            with patch.object(rate_limit, "_operation_limit", return_value=(1, 5)):
                first = await client.get(
                    "/__test/rate-limit",
                    headers={"Origin": "https://app.security.test"},
                )
                limited = await client.get(
                    "/__test/rate-limit",
                    headers={"Origin": "https://app.security.test"},
                )

        assert first.status_code == 200
        assert limited.status_code == 429
        retry_after = int(limited.headers["Retry-After"])
        assert retry_after > 0
        remaining_ms = await redis.pttl(key)
        assert remaining_ms > 0
        assert abs(retry_after - math.ceil(remaining_ms / 1000)) <= 1
        exposed = limited.headers.get("Access-Control-Expose-Headers", "")
        assert "retry-after" in exposed.lower()
    finally:
        await redis.delete(key)
        await close_redis()
