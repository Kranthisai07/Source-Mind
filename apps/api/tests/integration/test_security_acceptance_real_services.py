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
        assert document.ingestion_status == "pending"
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
        assert (first, second) == (1, 2)
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
