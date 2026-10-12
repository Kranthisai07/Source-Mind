"""End-to-end security acceptance against disposable PostgreSQL and Redis."""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk
from jose import jwt as jose_jwt
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.config import get_settings
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.exceptions import RateLimitExceededError, ServiceUnavailableError
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.models.workspace import Workspace
from tests.integration.test_security_foundation_real_db import (
    _seed_security_tenants,
)

_ISSUER = "https://clerk.security.test"
_AUTHORIZED_PARTY = "https://app.security.test"


def _configure_environment(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    redis_url: str,
) -> None:
    publishable_host = base64.b64encode(b"clerk.security.test$").decode()
    values = {
        "DATABASE_URL": database_url,
        "TEST_DATABASE_URL": database_url,
        "SECURITY_TEST_ALLOW_DISPOSABLE": "1",
        "REDIS_URL": redis_url,
        "TEST_REDIS_URL": redis_url,
        "ENVIRONMENT": "development",
        "DEBUG": "false",
        "AUTH_DEV_BYPASS_ENABLED": "false",
        "CLERK_SECRET_KEY": "sk_test_security_acceptance",
        "CLERK_PUBLISHABLE_KEY": f"pk_test_{publishable_host}",
        "CLERK_AUTHORIZED_PARTIES": f'["{_AUTHORIZED_PARTY}"]',
        "OPENAI_API_KEY": "test-openai-not-sent",
        "ANTHROPIC_API_KEY": "test-anthropic-not-sent",
        "URL_INGESTION_ENABLED": "false",
        "SLACK_MEMORY_COMMANDS_ENABLED": "false",
        "FF_NEO4J_ATTRIBUTION": "false",
        "FF_KAFKA_EVENTS": "false",
        "RATE_LIMIT_SEARCH_PER_MINUTE": "100",
        "RATE_LIMIT_INGESTION_PER_MINUTE": "100",
        "RATE_LIMIT_ANALYTICS_PER_MINUTE": "100",
        "RATE_LIMIT_WORKSPACE_CREATE_PER_HOUR": "100",
        "RATE_LIMIT_CONNECTOR_SYNC_PER_HOUR": "100",
        "RATE_LIMIT_SLACK_PER_MINUTE": "100",
        "RATE_LIMIT_FAIL_CLOSED": "true",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def _signing_material() -> tuple[bytes, dict[str, str]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_jwk = jwk.construct(public_pem, algorithm="RS256").to_dict()
    public_jwk["kid"] = "security-acceptance-key"
    return private_pem, public_jwk


def _token(private_pem: bytes, clerk_id: str) -> str:
    now = int(time.time())
    return jose_jwt.encode(
        {
            "sub": clerk_id,
            "sid": f"session-{clerk_id}",
            "iss": _ISSUER,
            "azp": _AUTHORIZED_PARTY,
            "iat": now,
            "nbf": now - 1,
            "exp": now + 300,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "security-acceptance-key", "typ": "JWT"},
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mutation_headers(token: str) -> dict[str, str]:
    return {
        **_auth(token),
        "Idempotency-Key": str(uuid.uuid4()),
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_signed_api_role_matrix_revocation_and_worker_reauthorization(
    supabase_engine,
    supabase_url: str,
    security_test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(monkeypatch, supabase_url, security_test_redis_url)
    data = await _seed_security_tenants(supabase_engine)
    private_pem, public_jwk = _signing_material()
    clerk_to_email = {
        clerk_id: f"{clerk_id}@example.com" for clerk_id in data.clerk_ids.values()
    }
    provisioned_clerk_id = f"provisioned-{uuid.uuid4().hex}"
    clerk_to_email[provisioned_clerk_id] = f"{provisioned_clerk_id}@example.com"

    async def fetch_profile(clerk_id: str, _secret: str) -> tuple[str, str]:
        return clerk_to_email[clerk_id], clerk_id

    from sourcemind.core import dependencies
    from sourcemind.core.redis_client import get_redis
    from sourcemind.main import create_app

    dependencies._jwks_cache.clear()
    dependencies._user_profile_cache.clear()
    app = create_app()
    tokens = {
        user_id: _token(private_pem, clerk_id)
        for user_id, clerk_id in data.clerk_ids.items()
    }
    provisioned_token = _token(private_pem, provisioned_clerk_id)

    with (
        patch(
            "sourcemind.core.dependencies._fetch_jwks",
            new=AsyncMock(return_value=[public_jwk]),
        ),
        patch(
            "sourcemind.core.dependencies._fetch_clerk_user_profile",
            new=fetch_profile,
        ),
    ):
        async with app.router.lifespan_context(app):
            await get_redis().flushdb()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://security.test",
            ) as client:
                health = await client.get("/health")
                assert health.status_code == 200
                assert health.json()["requests_since_start"] == 0
                assert health.json()["components"]["postgresql"]["pgvector"] == "enabled"
                assert health.json()["components"]["redis"]["status"] == "healthy"

                missing_auth = await client.get("/v1/workspaces")
                assert missing_auth.status_code == 401

                provisioned = await client.get(
                    "/v1/team/me", headers=_auth(provisioned_token)
                )
                assert provisioned.status_code == 200
                provisioned_user_id = uuid.UUID(provisioned.json()["data"]["id"])

                created_workspaces = []
                for index in (1, 2):
                    created = await client.post(
                        "/v1/workspaces",
                        headers=_mutation_headers(provisioned_token),
                        json={
                            "name": f"Provisioned Workspace {index}",
                            "slug": f"provisioned-{uuid.uuid4().hex[:12]}",
                        },
                    )
                    assert created.status_code == 201, created.text
                    created_workspaces.append(created.json()["data"])
                assert (
                    created_workspaces[0]["organization_id"]
                    == created_workspaces[1]["organization_id"]
                )
                listed = await client.get(
                    "/v1/workspaces", headers=_auth(provisioned_token)
                )
                assert listed.status_code == 200
                assert len(listed.json()["data"]) == 2

                admin_token = tokens[data.admin_id]
                member_token = tokens[data.member_id]
                viewer_token = tokens[data.viewer_id]
                other_token = tokens[data.other_workspace_member_id]
                zero_token = tokens[data.zero_membership_id]
                target_memory_path = f"/v1/memories/{data.target_memory_id}"

                admin_workspaces = await client.get(
                    "/v1/workspaces", headers=_auth(admin_token)
                )
                assert admin_workspaces.status_code == 200
                assert [row["id"] for row in admin_workspaces.json()["data"]] == [
                    str(data.target_workspace_id)
                ]

                for allowed_token in (admin_token, member_token, viewer_token):
                    response = await client.get(
                        target_memory_path,
                        headers=_auth(allowed_token),
                    )
                    assert response.status_code == 200, response.text
                    assert response.json()["data"]["content"].startswith(
                        "Populated tenant secret"
                    )

                other_positive = await client.get(
                    f"/v1/memories/{data.other_memory_id}",
                    headers=_auth(other_token),
                )
                assert other_positive.status_code == 200
                for denied_token in (other_token, zero_token):
                    response = await client.get(
                        target_memory_path,
                        headers=_auth(denied_token),
                    )
                    assert response.status_code == 404
                    response = await client.get(
                        f"/v1/workspaces/{data.target_workspace_id}/members",
                        headers=_auth(denied_token),
                    )
                    assert response.status_code == 404

                members = await client.get(
                    f"/v1/workspaces/{data.target_workspace_id}/members",
                    headers=_auth(admin_token),
                )
                assert members.status_code == 200
                assert len(members.json()["data"]) == 5

                viewer_write = await client.post(
                    f"/v1/memories?workspace_id={data.target_workspace_id}",
                    headers=_mutation_headers(viewer_token),
                    json={"content": "Viewer write must fail", "source_type": "text"},
                )
                assert viewer_write.status_code == 403

                disabled_url = await client.post(
                    f"/v1/memories?workspace_id={data.target_workspace_id}",
                    headers=_mutation_headers(member_token),
                    json={"url": "https://example.com", "source_type": "url"},
                )
                assert disabled_url.status_code == 422
                assert disabled_url.json()["error"]["message"] == "URL ingestion is disabled."

                queued = await client.post(
                    f"/v1/memories?workspace_id={data.target_workspace_id}",
                    headers=_mutation_headers(member_token),
                    json={
                        "content": "Queued then revoked worker acceptance payload",
                        "source_type": "text",
                    },
                )
                assert queued.status_code == 202, queued.text
                job_id = queued.json()["data"]["job_id"]
                document_id = uuid.UUID(queued.json()["data"]["document_id"])

                revoked = await client.delete(
                    f"/v1/workspaces/{data.target_workspace_id}/members/{data.member_id}",
                    headers=_mutation_headers(admin_token),
                )
                assert revoked.status_code == 204

                for path in (
                    target_memory_path,
                    f"/v1/memories/jobs/{job_id}",
                ):
                    response = await client.get(path, headers=_auth(member_token))
                    assert response.status_code == 404
                revoked_write = await client.post(
                    f"/v1/memories?workspace_id={data.target_workspace_id}",
                    headers=_mutation_headers(member_token),
                    json={"content": "Revoked write must fail", "source_type": "text"},
                )
                assert revoked_write.status_code == 404

        from sourcemind.workers.ingestion import _run_pipeline

        task = SimpleNamespace(
            request=SimpleNamespace(retries=0),
            retry=lambda **_kwargs: RuntimeError("unexpected retry"),
        )
        worker_result = await _run_pipeline(
            task,
            str(document_id),
            str(data.target_workspace_id),
            str(data.member_id),
        )
        assert worker_result == {
            "status": "rejected",
            "error": "Workspace access revoked",
        }

    async with AsyncSession(supabase_engine) as verify:
        await set_rls_user_context(verify, data.admin_id)
        await set_rls_workspace_context(verify, data.target_workspace_id)
        document = await verify.scalar(select(Document).where(Document.id == document_id))
        assert document is not None
        assert document.ingestion_status == "failed"
        assert document.error_message == "Workspace access revoked before ingestion."
        assert (document.pipeline_data or {}).get("current_stage") == "failed"
        memory_count = await verify.scalar(
            select(func.count(Memory.id)).where(Memory.document_id == document_id)
        )
        assert memory_count == 0


    async with AsyncSession(supabase_engine) as provisioned_verify:
        await set_rls_user_context(provisioned_verify, provisioned_user_id)
        provisioned_workspaces = (
            await provisioned_verify.execute(
                select(Workspace).where(
                    Workspace.created_by_user_id == provisioned_user_id
                )
            )
        ).scalars().all()
        assert len(provisioned_workspaces) == 2
        assert len({workspace.organization_id for workspace in provisioned_workspaces}) == 1

    get_settings.cache_clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_redis_counters_scopes_expiry_sharing_and_fail_closed(
    supabase_url: str,
    security_test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(monkeypatch, supabase_url, security_test_redis_url)
    from sourcemind.core import rate_limit
    from sourcemind.core.redis_client import close_redis, get_redis, init_redis

    await init_redis()
    redis = get_redis()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    other_workspace_id = uuid.uuid4()
    keys = [
        f"rate-limit:search:{workspace_id}:{user_id}",
        f"rate-limit:search:{workspace_id}:{other_user_id}",
        f"rate-limit:search:{other_workspace_id}:{user_id}",
    ]
    await redis.delete(*keys)

    with patch.object(rate_limit, "_operation_limit", return_value=(2, 60)):
        await enforce_rate_limit(RateLimitedOperation.SEARCH, user_id, workspace_id)
        assert await redis.get(keys[0]) == "1"
        assert 0 < await redis.ttl(keys[0]) <= 60
        await enforce_rate_limit(RateLimitedOperation.SEARCH, user_id, workspace_id)
        with pytest.raises(RateLimitExceededError):
            await enforce_rate_limit(RateLimitedOperation.SEARCH, user_id, workspace_id)
        await enforce_rate_limit(
            RateLimitedOperation.SEARCH, other_user_id, workspace_id
        )
        await enforce_rate_limit(
            RateLimitedOperation.SEARCH, user_id, other_workspace_id
        )

    expiry_user = uuid.uuid4()
    expiry_key = f"rate-limit:search:{workspace_id}:{expiry_user}"
    with patch.object(rate_limit, "_operation_limit", return_value=(1, 1)):
        await enforce_rate_limit(
            RateLimitedOperation.SEARCH, expiry_user, workspace_id
        )
        with pytest.raises(RateLimitExceededError):
            await enforce_rate_limit(
                RateLimitedOperation.SEARCH, expiry_user, workspace_id
            )
        await asyncio.sleep(1.1)
        await enforce_rate_limit(
            RateLimitedOperation.SEARCH, expiry_user, workspace_id
        )

    first_instance = Redis.from_url(security_test_redis_url, decode_responses=True)
    second_instance = Redis.from_url(security_test_redis_url, decode_responses=True)
    shared_key = f"rate-limit:search:{uuid.uuid4()}:{uuid.uuid4()}"
    try:
        first = await first_instance.eval(
            rate_limit._INCREMENT_WITH_TTL, 1, shared_key, 60
        )
        second = await second_instance.eval(
            rate_limit._INCREMENT_WITH_TTL, 1, shared_key, 60
        )
        assert (first[0], second[0]) == (1, 2)
        assert 0 < first[1] <= 60_000
        assert 0 < second[1] <= first[1]
    finally:
        await first_instance.delete(shared_key)
        await first_instance.aclose()
        await second_instance.aclose()

    unavailable = Redis.from_url(
        "redis://127.0.0.1:56378/15",
        decode_responses=True,
        socket_connect_timeout=0.1,
    )
    try:
        with (
            patch("sourcemind.core.rate_limit.get_redis", return_value=unavailable),
            pytest.raises(ServiceUnavailableError),
        ):
            await enforce_rate_limit(
                RateLimitedOperation.SEARCH, uuid.uuid4(), uuid.uuid4()
            )
    finally:
        await unavailable.aclose()
        await redis.delete(*keys, expiry_key)
        await close_redis()
        get_settings.cache_clear()


# ── Friends-release journey: sign in -> workspace -> add -> create -> edit -> search ──
#
# One ordinary-user journey over the real FastAPI app, real PostgreSQL (restricted
# role, RLS) and real Redis, with signed Clerk tokens. Everything that would
# call a provider is replaced in-process and nothing touches the network: Clerk
# profile/verification lookups, the ingestion worker's document extraction,
# chunking, fact extraction, embeddings and relation detection, the PATCH
# re-embedding, the search query embedding, and the attribution scorer (a
# deterministic equal split; spaCy cannot load on this Python).


class _JourneyEmbedding:
    """Constant unit-ish vector so semantic search is deterministic and offline."""

    vector = [0.01] * 3072


def _stub_ingestion_providers(monkeypatch: pytest.MonkeyPatch, fact: str) -> None:
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    from sourcemind.services.attribution import engine as attribution_engine
    from sourcemind.services.attribution import scorer as attribution_scorer
    from sourcemind.services.attribution import versioning
    from sourcemind.services.ingestion import chunker, embedder, extractor, fact_extractor
    from sourcemind.services.ingestion.chunker import ChunkResult
    from sourcemind.services.ingestion.embedder import EmbeddingResult
    from sourcemind.services.ingestion.extractor import ExtractionResult as DocumentExtraction
    from sourcemind.services.ingestion.fact_extractor import ExtractionResult as FactExtraction
    from sourcemind.services.memory import relations

    def equal_split(edits):
        users = list(dict.fromkeys(e.user_id for e in edits))
        return [
            SimpleNamespace(
                user_id=user,
                contribution_weight=1 / len(users),
                char_diff_score=0.5,
                semantic_score=0.5,
                temporal_score=0.5,
                structural_score=0.5,
                approval_score=0.0,
            )
            for user in users
        ]

    scorer = SimpleNamespace(compute_scores=equal_split)
    monkeypatch.setattr(attribution_engine, "get_scorer", lambda: scorer)
    monkeypatch.setattr(attribution_scorer, "get_scorer", lambda: scorer)

    async def extract_document(**_kwargs):
        return DocumentExtraction(content=fact, content_type="text")

    async def chunk_document(_extraction):
        return [
            ChunkResult(
                content=fact, token_count=8, chunk_index=0, total_chunks=1, chunk_type="text"
            )
        ]

    async def extract_facts(*_args, **_kwargs):
        return FactExtraction(facts=[fact], total_chunks=1, failed_chunks=0, failure_reasons=[])

    async def embed(texts):
        return [
            EmbeddingResult(
                content=t, embedding=_JourneyEmbedding.vector, token_count=8, cached=False
            )
            for t in texts
        ]

    async def no_relations(*_args, **_kwargs):
        return []

    monkeypatch.setattr(extractor, "extract", extract_document)
    monkeypatch.setattr(chunker, "chunk", chunk_document)
    monkeypatch.setattr(
        fact_extractor, "FactExtractor", lambda _c: SimpleNamespace(extract=extract_facts)
    )
    monkeypatch.setattr(embedder, "EmbeddingService", lambda _c: SimpleNamespace(embed=embed))
    # PATCH re-embeds through the name versioning.py imported at module load.
    monkeypatch.setattr(versioning, "EmbeddingService", lambda _c: SimpleNamespace(embed=embed))
    monkeypatch.setattr(
        relations,
        "RelationDetector",
        lambda _c: SimpleNamespace(detect=no_relations, plan=no_relations, apply=no_relations),
    )
    monkeypatch.setattr(AsyncOpenAI, "__new__", lambda cls, **_kwargs: object())
    monkeypatch.setattr(AsyncAnthropic, "__new__", lambda cls, **_kwargs: object())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_friends_release_journey_over_http_with_signed_tokens(
    supabase_engine,
    supabase_url: str,
    security_test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_environment(monkeypatch, supabase_url, security_test_redis_url)
    original_fact = "Deploy freezes start every Friday at noon."
    edited_fact = "Deploy freezes start every Thursday at noon."
    _stub_ingestion_providers(monkeypatch, original_fact)

    suffix = uuid.uuid4().hex[:12]
    people = {
        name: (f"journey-{name}-{suffix}", f"journey-{name}-{suffix}@example.com")
        for name in ("a", "b", "c")
    }
    email_of = dict(people.values())
    private_pem, public_jwk = _signing_material()
    tokens = {name: _token(private_pem, clerk) for name, (clerk, _email) in people.items()}

    async def fetch_profile(clerk_id: str, _secret: str) -> tuple[str, str]:
        return email_of[clerk_id], clerk_id

    async def verified_primary(clerk_id: str, _secret: str) -> tuple[str, str]:
        return email_of[clerk_id], "verified"

    async def fake_embeddings_create(**_kwargs):
        return SimpleNamespace(data=[SimpleNamespace(embedding=_JourneyEmbedding.vector)])

    fake_openai = SimpleNamespace(embeddings=SimpleNamespace(create=fake_embeddings_create))

    from sourcemind.core import dependencies
    from sourcemind.core.redis_client import get_redis
    from sourcemind.main import create_app
    from sourcemind.workers import ingestion as worker

    dependencies._jwks_cache.clear()
    dependencies._user_profile_cache.clear()
    app = create_app()

    async def override_openai() -> object:
        return fake_openai

    app.dependency_overrides[dependencies.get_openai_client] = override_openai
    monkeypatch.setattr(
        worker.process_document,
        "apply_async",
        lambda **_kwargs: SimpleNamespace(id=f"journey-task-{uuid.uuid4()}"),
    )

    with (
        patch("sourcemind.core.dependencies._fetch_jwks", new=AsyncMock(return_value=[public_jwk])),
        patch("sourcemind.core.dependencies._fetch_clerk_user_profile", new=fetch_profile),
        patch(
            "sourcemind.api.v1.workspaces.fetch_clerk_primary_email_verification",
            new=verified_primary,
        ),
    ):
        async with app.router.lifespan_context(app):
            await get_redis().flushdb()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://journey.test"
            ) as client:
                # 1. Everyone signs in once (just-in-time user rows).
                user_ids = {}
                for name in ("a", "b", "c"):
                    me = await client.get("/v1/team/me", headers=_auth(tokens[name]))
                    assert me.status_code == 200, me.text
                    user_ids[name] = uuid.UUID(me.json()["data"]["id"])

                # 2. A creates a NEW workspace.
                created = await client.post(
                    "/v1/workspaces",
                    headers=_mutation_headers(tokens["a"]),
                    json={"name": "Friends", "slug": f"friends-{suffix}"},
                )
                assert created.status_code == 201, created.text
                workspace_id = created.json()["data"]["id"]

                # B and C cannot see it yet.
                for name in ("b", "c"):
                    not_yet = await client.get(
                        f"/v1/workspaces/{workspace_id}", headers=_auth(tokens[name])
                    )
                    assert not_yet.status_code == 404
                    assert not_yet.json()["error"]["code"] == "SM022"

                # 3. A adds B by email (Clerk confirms the verified primary email).
                added = await client.post(
                    f"/v1/workspaces/{workspace_id}/members",
                    headers=_mutation_headers(tokens["a"]),
                    json={"email": people["b"][1]},
                )
                assert added.status_code == 201, added.text
                assert added.json()["data"]["role"] == "member"
                assert added.json()["data"]["user"]["id"] == str(user_ids["b"])
                repeat = await client.post(
                    f"/v1/workspaces/{workspace_id}/members",
                    headers=_mutation_headers(tokens["a"]),
                    json={"email": people["b"][1]},
                )
                assert repeat.status_code == 409
                assert repeat.json()["error"]["details"] == {"reason": "already_member"}

                # 4. B lists workspaces and sees it; C still does not.
                listed = await client.get("/v1/workspaces", headers=_auth(tokens["b"]))
                assert [w["id"] for w in listed.json()["data"]] == [workspace_id]
                assert (
                    await client.get("/v1/workspaces", headers=_auth(tokens["c"]))
                ).json()["data"] == []

                # 5. B creates a memory (extract mode); the worker runs in-process.
                queued = await client.post(
                    f"/v1/memories?workspace_id={workspace_id}",
                    headers=_mutation_headers(tokens["b"]),
                    json={
                        "content": "Notes from the release meeting about deploy freezes.",
                        "source_type": "text",
                        "ingestion_mode": "extract",
                    },
                )
                assert queued.status_code == 202, queued.text
                job_id = queued.json()["data"]["job_id"]
                document_id = queued.json()["data"]["document_id"]

                from tests.integration.test_ingestion_worker_contract import FakeTask

                result = await worker._run_pipeline(
                    FakeTask(retries=0), document_id, workspace_id, str(user_ids["b"])
                )
                assert result["status"] == "completed", result
                # The worker closes the Redis pool when it finishes (it normally
                # owns its own process); reopen it for the API side of the app.
                from sourcemind.core.redis_client import init_redis

                await init_redis()
                job = await client.get(f"/v1/memories/jobs/{job_id}", headers=_auth(tokens["b"]))
                assert job.status_code == 200, job.text
                assert job.json()["data"]["status"] == "completed"
                assert job.json()["data"]["memory_count"] == 1
                v1 = job.json()["data"]["memory_ids"][0]

                # 6. B edits it; the old version is now superseded.
                edited = await client.patch(
                    f"/v1/memories/{v1}",
                    headers=_mutation_headers(tokens["b"]),
                    json={"content": edited_fact},
                )
                assert edited.status_code == 200, edited.text
                v2 = edited.json()["data"]["id"]
                assert v2 != v1 and edited.json()["data"]["version"] == 2

                # 7. A searches and finds the EDITED version, not the original.
                found = await client.post(
                    f"/v1/memories/search?workspace_id={workspace_id}",
                    headers=_auth(tokens["a"]),
                    json={"query": "when do deploy freezes start", "mode": "hybrid"},
                )
                assert found.status_code == 200, found.text
                hits = {r["memory"]["id"]: r["memory"]["content"] for r in found.json()["results"]}
                assert hits.get(v2) == edited_fact, hits
                assert v1 not in hits

                # 8. B patches the STALE version: 409 pointing at the latest.
                stale = await client.patch(
                    f"/v1/memories/{v1}",
                    headers=_mutation_headers(tokens["b"]),
                    json={"content": "A third opinion on the freeze day."},
                )
                assert stale.status_code == 409, stale.text
                assert stale.json()["error"]["code"] == "SM035"
                assert stale.json()["error"]["details"] == {
                    "memory_id": v1,
                    "latest_memory_id": v2,
                    "reason": "superseded",
                }
                old_read = await client.get(f"/v1/memories/{v1}", headers=_auth(tokens["a"]))
                assert old_read.status_code == 200
                assert old_read.json()["data"]["is_current"] is False
                assert old_read.json()["data"]["latest_memory_id"] == v2

                # 9. C, never added, is blind to the whole workspace.
                for method, path, kwargs in (
                    ("GET", f"/v1/workspaces/{workspace_id}", {}),
                    ("GET", f"/v1/workspaces/{workspace_id}/members", {}),
                    ("GET", f"/v1/memories/{v2}", {}),
                    ("POST", f"/v1/memories/search?workspace_id={workspace_id}",
                     {"json": {"query": "deploy freezes"}}),
                ):
                    headers = (
                        _mutation_headers(tokens["c"]) if method == "POST" else _auth(tokens["c"])
                    )
                    denied = await client.request(method, path, headers=headers, **kwargs)
                    assert denied.status_code == 404, (path, denied.status_code, denied.text)
                c_stale = await client.patch(
                    f"/v1/memories/{v1}",
                    headers=_mutation_headers(tokens["c"]),
                    json={"content": "Probe."},
                )
                assert c_stale.status_code == 404
                assert c_stale.json()["error"]["code"] == "SM020"

    async with AsyncSession(supabase_engine) as verify:
        await set_rls_user_context(verify, user_ids["a"])
        await set_rls_workspace_context(verify, uuid.UUID(workspace_id))
        versions = (
            await verify.execute(
                select(Memory.version, Memory.current_version)
                .where(Memory.workspace_id == uuid.UUID(workspace_id))
                .order_by(Memory.version)
            )
        ).all()
        assert [(v, c) for v, c in versions] == [(1, False), (2, True)]
    get_settings.cache_clear()
