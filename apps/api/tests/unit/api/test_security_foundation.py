"""Focused regressions for the security foundation controls."""

from __future__ import annotations

import ast
import inspect
import socket
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk
from jose import jwt as jose_jwt
from slack_sdk.signature import SignatureVerifier

from sourcemind.core.dependencies import (
    WorkspacePermission,
    _verify_clerk_token,
    require_workspace_permission,
)
from sourcemind.core.exceptions import (
    RateLimitExceededError,
    ServiceUnavailableError,
    TokenInvalidError,
    UnauthorizedError,
    ValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceNotFoundError,
)
from sourcemind.core.rate_limit import RateLimitedOperation, enforce_rate_limit
from sourcemind.core.url_security import validate_public_url
from sourcemind.schemas.connector import ConnectorResponse, SyncLogResponse
from sourcemind.services.slack.bot import _resolve_slack_identity


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
    public_jwk["kid"] = "security-test-key"
    return private_pem, public_jwk


def _session_token(private_pem: bytes, **overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": "user_security_test",
        "sid": "sess_security_test",
        "iss": "https://clerk.example.test",
        "azp": "https://app.example.test",
        "iat": now,
        "nbf": now - 1,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jose_jwt.encode(
        claims,
        private_pem,
        algorithm="RS256",
        headers={"kid": "security-test-key", "typ": "JWT"},
    )


async def _verify(token: str, public_jwk: dict[str, str]) -> dict:
    with patch(
        "sourcemind.core.dependencies._fetch_jwks",
        new=AsyncMock(return_value=[public_jwk]),
    ):
        return await _verify_clerk_token(
            token,
            "https://clerk.example.test/.well-known/jwks.json",
            issuer="https://clerk.example.test",
            authorized_parties=["https://app.example.test"],
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_session_token_accepts_expected_issuer_and_party() -> None:
    private_pem, public_jwk = _signing_material()
    claims = await _verify(_session_token(private_pem), public_jwk)
    assert claims["sub"] == "user_security_test"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_session_token_rejects_wrong_issuer() -> None:
    private_pem, public_jwk = _signing_material()
    token = _session_token(private_pem, iss="https://attacker.example")
    with pytest.raises(TokenInvalidError):
        await _verify(token, public_jwk)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_real_session_token_rejects_wrong_authorized_party() -> None:
    private_pem, public_jwk = _signing_material()
    token = _session_token(private_pem, azp="https://attacker.example")
    with pytest.raises(TokenInvalidError):
        await _verify(token, public_jwk)


def _membership_session(role: str | None) -> AsyncMock:
    session = AsyncMock()
    session.info = {}
    result = MagicMock()
    result.first.return_value = MagicMock(role=role) if role else None
    session.execute = AsyncMock(side_effect=[result, MagicMock()])
    return session


@pytest.mark.unit
@pytest.mark.asyncio
async def test_zero_memberships_and_other_workspace_membership_are_denied() -> None:
    for _caller_state in ("zero-memberships", "member-of-different-workspace"):
        with pytest.raises(WorkspaceNotFoundError):
            await require_workspace_permission(
                _membership_session(None),
                uuid.uuid4(),
                uuid.uuid4(),
                WorkspacePermission.READ,
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_viewer_cannot_contribute_but_member_can() -> None:
    with pytest.raises(WorkspaceAccessDeniedError):
        await require_workspace_permission(
            _membership_session("viewer"),
            uuid.uuid4(),
            uuid.uuid4(),
            WorkspacePermission.CONTRIBUTE,
        )
    role = await require_workspace_permission(
        _membership_session("member"),
        uuid.uuid4(),
        uuid.uuid4(),
        WorkspacePermission.CONTRIBUTE,
    )
    assert role == "member"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admin_cannot_begin_an_owner_departure() -> None:
    from sourcemind.api.v1 import team
    from sourcemind.models.workspace import WorkspaceRole

    membership_result = MagicMock()
    membership_result.scalar_one_or_none.return_value = SimpleNamespace(
        role=WorkspaceRole.OWNER
    )
    owners_result = MagicMock()
    owners_result.scalars.return_value.all.return_value = [
        uuid.uuid4(),
        uuid.uuid4(),
    ]
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[membership_result, owners_result])
    body = team.InitiateHandoffBody(departing_user_id=uuid.uuid4())
    current_user = SimpleNamespace(user_id=uuid.uuid4())

    with patch(
        "sourcemind.api.v1.team.require_workspace_permission",
        new=AsyncMock(return_value=WorkspaceRole.ADMIN.value),
    ):
        with pytest.raises(WorkspaceAccessDeniedError, match="Only an owner"):
            await team.initiate_handoff(
                workspace_id=uuid.uuid4(),
                body=body,
                db=session,
                current_user=current_user,
                request_id="security-test",
            )

    assert session.execute.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_membership_query_requires_active_non_departed_state() -> None:
    session = _membership_session(None)
    with pytest.raises(WorkspaceNotFoundError):
        await require_workspace_permission(
            session,
            uuid.uuid4(),
            uuid.uuid4(),
            WorkspacePermission.READ,
        )
    sql = str(session.execute.await_args_list[0].args[0])
    assert "wm.status = 'active'" in sql
    assert "wm.departed_at IS NULL" in sql


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/",
        "http://service.internal/",
        "https://user:password@example.com/",
        "https://example.com:8443/",
    ],
)
async def test_url_policy_rejects_local_internal_and_credentialed_targets(url: str) -> None:
    with pytest.raises(ValidationError):
        await validate_public_url(url)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_url_policy_rejects_hostname_if_any_dns_answer_is_private() -> None:
    loop = MagicMock()
    loop.getaddrinfo = AsyncMock(
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ]
    )
    with patch("sourcemind.core.url_security.asyncio.get_running_loop", return_value=loop):
        with pytest.raises(ValidationError):
            await validate_public_url("https://example.com/article")


@pytest.mark.unit
def test_connector_response_recursively_redacts_credentials() -> None:
    response = ConnectorResponse(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        connector_type="github",
        display_name="Security test",
        config={
            "token": "top-secret",
            "nested": {"private_key": "private", "repository": "org/repo"},
        },
        status="active",
        last_sync_at=None,
        next_sync_at=None,
        created_at=datetime.now(UTC),
    ).model_dump()
    assert response["config"]["token"] == "[REDACTED]"
    assert response["config"]["nested"]["private_key"] == "[REDACTED]"
    assert response["config"]["nested"]["repository"] == "org/repo"


@pytest.mark.unit
def test_sync_log_hides_provider_error_details() -> None:
    response = SyncLogResponse(
        id=uuid.uuid4(),
        connector_id=uuid.uuid4(),
        sync_type="incremental",
        status="failed",
        artifacts_found=0,
        artifacts_new=0,
        artifacts_skipped=0,
        error_message="Authorization: Bearer leaked-token",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    ).model_dump()
    assert response["error_message"] == "Connector sync failed."


@pytest.mark.unit
def test_slack_identity_requires_exact_team_channel_and_user_mapping() -> None:
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = SimpleNamespace(
        slack_memory_commands_enabled=True,
        slack_installations={
            "T_ALLOWED": {
                "workspace_id": str(workspace_id),
                "allowed_channel_ids": ["C_ALLOWED"],
                "users": {"U_ALLOWED": str(user_id)},
            }
        },
    )
    with patch("sourcemind.services.slack.bot.get_settings", return_value=settings):
        assert _resolve_slack_identity(
            {"team_id": "T_ALLOWED", "channel_id": "C_ALLOWED", "user_id": "U_ALLOWED"}
        ) == (workspace_id, user_id)
        with pytest.raises(UnauthorizedError):
            _resolve_slack_identity(
                {"team_id": "T_ALLOWED", "channel_id": "C_OTHER", "user_id": "U_ALLOWED"}
            )


@pytest.mark.unit
def test_slack_transport_accepts_valid_signature_and_rejects_forgery_and_replay() -> None:
    signing_secret = "security-test-signing-secret"
    body = "command=%2Fmemory&text=approved+query"
    verifier = SignatureVerifier(signing_secret=signing_secret)
    timestamp = str(int(time.time()))
    signature = verifier.generate_signature(timestamp=timestamp, body=body)

    assert signature is not None
    assert verifier.is_valid(body=body, timestamp=timestamp, signature=signature)
    assert not verifier.is_valid(
        body=body,
        timestamp=timestamp,
        signature="v0=" + ("0" * 64),
    )

    replay_timestamp = str(int(timestamp) - 301)
    replay_signature = verifier.generate_signature(
        timestamp=replay_timestamp,
        body=body,
    )
    assert replay_signature is not None
    assert not verifier.is_valid(
        body=body,
        timestamp=replay_timestamp,
        signature=replay_signature,
    )


@pytest.mark.unit
def test_slack_endpoint_installs_bolt_request_verification() -> None:
    from sourcemind.api.v1 import slack

    settings = SimpleNamespace(
        slack_memory_commands_enabled=True,
        slack_bot_token="xoxb-security-test",
        slack_signing_secret="security-test-signing-secret",
        sourcemind_app_url="https://app.example.test",
    )
    slack._bolt_app = None
    slack._handler = None
    try:
        with patch("sourcemind.api.v1.slack.get_settings", return_value=settings):
            slack._get_handler()
        middleware_names = {
            type(middleware).__name__
            for middleware in slack._bolt_app._async_middleware_list
        }
        assert "AsyncRequestVerification" in middleware_names
    finally:
        slack._bolt_app = None
        slack._handler = None


@pytest.mark.unit
def test_slack_endpoint_is_disabled_unless_explicitly_enabled() -> None:
    from sourcemind.api.v1 import slack

    settings = SimpleNamespace(slack_memory_commands_enabled=False)
    slack._bolt_app = None
    slack._handler = None
    with patch("sourcemind.api.v1.slack.get_settings", return_value=settings):
        with pytest.raises(RuntimeError, match="disabled"):
            slack._get_handler()


@pytest.mark.unit
def test_every_workspace_route_calls_a_central_authorization_guard() -> None:
    from sourcemind.api.v1 import (
        analytics,
        conflicts,
        connectors,
        memories,
        search,
        team,
        workspaces,
    )

    guard_names = {
        "require_workspace_member",
        "require_workspace_permission",
        "require_memory_access",
        "require_conflict_access",
        "require_connector_access",
    }
    self_scoped_or_bootstrap_routes = {
        "create_workspace",
        "get_current_user_profile",
        "list_workspaces",
    }
    missing_guards: list[str] = []

    for module in (
        analytics,
        conflicts,
        connectors,
        memories,
        search,
        team,
        workspaces,
    ):
        tree = ast.parse(inspect.getsource(module))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            is_route = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in {"delete", "get", "patch", "post", "put"}
                for decorator in node.decorator_list
            )
            if not is_route or node.name in self_scoped_or_bootstrap_routes:
                continue
            called_names = {
                call.func.id
                for call in ast.walk(node)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
            if not called_names.intersection(guard_names):
                missing_guards.append(f"{module.__name__}.{node.name}")

    assert not missing_guards, (
        "workspace/resource routes without a central authorization guard: "
        + ", ".join(missing_guards)
    )


def _rate_settings(*, fail_closed: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        rate_limit_search_per_minute=1,
        rate_limit_ingestion_per_minute=1,
        rate_limit_analytics_per_minute=1,
        rate_limit_workspace_create_per_hour=1,
        rate_limit_connector_sync_per_hour=1,
        rate_limit_slack_per_minute=1,
        rate_limit_fail_closed=fail_closed,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_atomic_rate_limit_rejects_over_limit() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 2
    with patch("sourcemind.core.rate_limit.get_redis", return_value=redis), patch(
        "sourcemind.core.rate_limit.get_settings", return_value=_rate_settings()
    ):
        with pytest.raises(RateLimitExceededError):
            await enforce_rate_limit(RateLimitedOperation.SEARCH, uuid.uuid4(), uuid.uuid4())
    redis.eval.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_backend_failure_fails_closed() -> None:
    redis = AsyncMock()
    redis.eval.side_effect = ConnectionError("redis unavailable")
    with patch("sourcemind.core.rate_limit.get_redis", return_value=redis), patch(
        "sourcemind.core.rate_limit.get_settings", return_value=_rate_settings()
    ):
        with pytest.raises(ServiceUnavailableError):
            await enforce_rate_limit(RateLimitedOperation.SEARCH, uuid.uuid4(), uuid.uuid4())
