"""GitHub App connect flow: user token exchange, tickets, intersection, strict config."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import structlog
from pydantic import ValidationError as PydanticValidationError

from sourcemind.api.v1 import connectors as api
from sourcemind.connectors.github import connect_flow
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.core.exceptions import ForbiddenError
from sourcemind.schemas.connector import ConnectorCreateRequest, GitHubConnectorConfig
from sourcemind.services.connectors import connect_ticket

USER_TOKEN = "ghu_SUPER_SECRET_USER_TOKEN"  # noqa: S105 - test value
SECRET = "client-secret-for-tests"  # noqa: S105


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, key):
        return 1 if self.data.pop(key, None) is not None else 0

    async def eval(self, *_a):
        return [1, 1000]


@pytest.fixture
def redis():
    fake = FakeRedis()
    with patch.object(connect_ticket, "get_redis", return_value=fake):
        yield fake


def _github_transport(*, installs, repos_by_install, calls=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/login/oauth/access_token":
            if b"code=bad" in request.content:
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": USER_TOKEN})
        assert request.headers["authorization"] == f"Bearer {USER_TOKEN}"
        if path == "/user":
            return httpx.Response(200, json={"id": 4242, "login": "octo"})
        if path == "/user/installations":
            return httpx.Response(
                200, json={"installations": [{"id": i} for i in installs]}
            )
        if path.startswith("/user/installations/") and path.endswith("/repositories"):
            iid = int(path.split("/")[3])
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {"full_name": name, "private": True, "default_branch": "main"}
                        for name in repos_by_install[iid]
                    ]
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _authorize(transport, **kw):
    async with httpx.AsyncClient(transport=transport) as http:
        return await connect_flow.authorize_user(
            "good", client_id="cid", client_secret=SECRET, http=http, **kw
        )


# ── user token exchange ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_repo_list_is_the_only_output_and_has_no_token() -> None:
    transport = _github_transport(installs=[7], repos_by_install={7: ["o/a"]})
    result = await _authorize(transport)
    assert [r.full_name for r in result.repositories] == ["o/a"]
    assert (result.github_user_id, result.installation_id) == (4242, 7)
    assert USER_TOKEN not in repr(result)
    assert "token" not in set(result.__dataclass_fields__)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_installation_choice_required_and_validated() -> None:
    transport = _github_transport(installs=[7, 8], repos_by_install={7: ["o/a"], 8: ["o/b"]})
    with pytest.raises(connect_flow.ConnectFlowError) as e1:
        await _authorize(transport)
    assert e1.value.code == "installation_required"
    with pytest.raises(connect_flow.ConnectFlowError) as e2:
        await _authorize(transport, installation_id=99)
    assert e2.value.code == "installation_not_reachable"
    assert (await _authorize(transport, installation_id=8)).installation_id == 8


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rejected_code_is_a_stable_error() -> None:
    transport = _github_transport(installs=[7], repos_by_install={7: []})
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(connect_flow.ConnectFlowError) as exc:
            await connect_flow.authorize_user(
                "bad", client_id="cid", client_secret=SECRET, http=http
            )
    assert exc.value.code == "code_rejected"
    assert USER_TOKEN not in str(exc.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_token_never_logged() -> None:
    transport = _github_transport(installs=[7], repos_by_install={7: ["o/a"]})
    with structlog.testing.capture_logs() as captured:
        await _authorize(transport)
    assert USER_TOKEN not in json.dumps(captured, default=str)
    assert SECRET not in json.dumps(captured, default=str)


# ── state ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_state_roundtrip_replay_and_other_user(redis) -> None:
    ws, user = uuid.uuid4(), uuid.uuid4()
    state = await connect_ticket.issue_state(client_secret=SECRET, workspace_id=ws, user_id=user)
    with pytest.raises(connect_ticket.ConnectTicketError):  # other user
        await connect_ticket.consume_state(
            client_secret=SECRET, state=state, user_id=uuid.uuid4()
        )
    assert await connect_ticket.consume_state(
        client_secret=SECRET, state=state, user_id=user
    ) == ws
    with pytest.raises(connect_ticket.ConnectTicketError):  # replay
        await connect_ticket.consume_state(client_secret=SECRET, state=state, user_id=user)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_forged_and_expired_state_rejected(redis) -> None:
    ws, user = uuid.uuid4(), uuid.uuid4()
    state = await connect_ticket.issue_state(client_secret=SECRET, workspace_id=ws, user_id=user)
    nonce = state.split(".")[0]
    for forged in (f"{nonce}.{'0' * 64}", "garbage", f"{nonce}."):
        with pytest.raises(connect_ticket.ConnectTicketError):
            await connect_ticket.consume_state(client_secret=SECRET, state=forged, user_id=user)
    redis.data.clear()  # TTL expiry
    with pytest.raises(connect_ticket.ConnectTicketError):
        await connect_ticket.consume_state(client_secret=SECRET, state=state, user_id=user)


# ── ticket ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_ticket_scoped_to_workspace_and_user_and_single_use(redis) -> None:
    ws, user = uuid.uuid4(), uuid.uuid4()
    tid = await connect_ticket.store_ticket(
        workspace_id=ws, user_id=user, github_user_id=1, installation_id=2, repositories=["o/A"]
    )
    assert USER_TOKEN not in json.dumps(redis.data)
    for bad_ws, bad_user in ((uuid.uuid4(), user), (ws, uuid.uuid4())):
        with pytest.raises(connect_ticket.ConnectTicketError):
            await connect_ticket.load_ticket(tid, workspace_id=bad_ws, user_id=bad_user)
    with pytest.raises(connect_ticket.ConnectTicketError):
        await connect_ticket.load_ticket("forged", workspace_id=ws, user_id=user)
    ticket = await connect_ticket.load_ticket(tid, workspace_id=ws, user_id=user)
    assert ticket.canonical_repo("O/a") == "o/A"
    assert await connect_ticket.claim_ticket(tid) is True
    assert await connect_ticket.claim_ticket(tid) is False  # replay
    with pytest.raises(connect_ticket.ConnectTicketError):  # expired / consumed
        await connect_ticket.load_ticket(tid, workspace_id=ws, user_id=user)


# ── strict config ────────────────────────────────────────────────────────────

_GOOD = {"repos": ["o/a"], "consent_workspace_readable": True}


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra",
    [
        {"token": "x"},
        {"github_token": "x"},
        {"api_key": "x"},
        {"private_key": "x"},
        {"webhook_secret": "x"},
        {"installation_id": 5},
        {"authorized_github_user_id": 5},
        {"anything_else": 1},
    ],
)
def test_unknown_or_secret_like_config_keys_rejected(extra) -> None:
    with pytest.raises(PydanticValidationError) as exc:
        ConnectorCreateRequest(
            connector_type="github",
            display_name="x",
            config={**_GOOD, **extra},
            ticket_id="t" * 20,
        )
    # the rejection names the field path and error type only, never the submitted value
    assert "extra_forbidden" in str(exc.value)


@pytest.mark.unit
@pytest.mark.parametrize("connector_type", ["slack", "discord", "notion"])
def test_unsupported_connector_types_rejected(connector_type) -> None:
    with pytest.raises(PydanticValidationError, match="not supported yet"):
        ConnectorCreateRequest(
            connector_type=connector_type, display_name="x", config={}, ticket_id="t" * 20
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "config",
    [
        {"repos": [], "consent_workspace_readable": True},
        {"repos": ["noslash"], "consent_workspace_readable": True},
        {"repos": ["a/b/c"], "consent_workspace_readable": True},
        {"repos": ["../etc"], "consent_workspace_readable": True},
        {"repos": ["o/a", "O/A"], "consent_workspace_readable": True},
        {"repos": [f"o/r{i}" for i in range(6)], "consent_workspace_readable": True},
        {"repos": ["o/a"]},
        {"repos": ["o/a"], "consent_workspace_readable": False},
        {"repos": ["o/a"], "consent_workspace_readable": True, "artifact_types": ["files"]},
        {"repos": ["o/a"], "consent_workspace_readable": True, "artifact_types": []},
    ],
)
def test_invalid_github_config_rejected(config) -> None:
    with pytest.raises(PydanticValidationError):
        GitHubConnectorConfig.model_validate(config)


@pytest.mark.unit
def test_create_requires_ticket_and_accepts_good_config() -> None:
    with pytest.raises(PydanticValidationError, match="ticket_id"):
        ConnectorCreateRequest(connector_type="github", display_name="x", config=_GOOD)
    req = ConnectorCreateRequest(
        connector_type="github",
        display_name="x",
        config={**_GOOD, "artifact_types": ["issues", "issues"]},
        ticket_id="t" * 20,
    )
    assert req.config["artifact_types"] == ["issues"]


# ── route: intersection (T-U1/T-U2/T-U6) ─────────────────────────────────────

def _user() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=uuid.uuid4(), clerk_id="c", email="a@example.com")


async def _create(db, user, ws, repos, ticket_id):
    body = ConnectorCreateRequest(
        connector_type="github",
        display_name="n",
        config={"repos": repos, "consent_workspace_readable": True},
        ticket_id=ticket_id,
    )
    with patch.object(api, "require_workspace_permission", AsyncMock()):
        return await api.create_connector(ws, body, db, user, "rid")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_repo_outside_user_intersection_is_403_and_nothing_stored(redis) -> None:
    """T-U1: installation reaches {A,B}; the user's token reaches {A}; B is rejected."""
    user, ws = _user(), uuid.uuid4()
    transport = _github_transport(installs=[7], repos_by_install={7: ["o/A"]})
    auth = await _authorize(transport)
    tid = await connect_ticket.store_ticket(
        workspace_id=ws,
        user_id=user.user_id,
        github_user_id=auth.github_user_id,
        installation_id=auth.installation_id,
        repositories=[r.full_name for r in auth.repositories],
    )
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    with pytest.raises(ForbiddenError) as exc:
        await _create(db, user, ws, ["o/A", "o/B"], tid)
    assert exc.value.http_status == 403
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    assert connect_ticket._TICKET_PREFIX + tid in redis.data  # not burned by a refusal


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["forged", "other_user", "other_workspace"])
async def test_bad_ticket_rejected_nothing_stored(redis, who) -> None:
    user, ws = _user(), uuid.uuid4()
    tid = await connect_ticket.store_ticket(
        workspace_id=ws if who != "other_workspace" else uuid.uuid4(),
        user_id=user.user_id if who != "other_user" else uuid.uuid4(),
        github_user_id=1,
        installation_id=2,
        repositories=["o/a"],
    )
    if who == "forged":
        tid = "forged" * 5
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    with pytest.raises(ForbiddenError):
        await _create(db, user, ws, ["o/a"], tid)
    db.add.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_valid_ticket_binds_installation_and_is_single_use(redis) -> None:
    user, ws = _user(), uuid.uuid4()
    tid = await connect_ticket.store_ticket(
        workspace_id=ws, user_id=user.user_id, github_user_id=4242, installation_id=7,
        repositories=["o/A"],
    )
    saved = {}

    async def fake_create(db, **kw):
        saved.update(kw)
        return SimpleNamespace(
            id=uuid.uuid4(), workspace_id=ws, connector_type="github",
            display_name="n", config={"repos": kw["repos"]}, status="active",
            last_sync_at=None, next_sync_at=None, created_at=__import__("datetime").datetime.now(
                __import__("datetime").UTC),
            installation_id=kw["installation_id"],
            authorized_github_user_id=kw["authorized_github_user_id"],
            created_by=kw["created_by"], generation=0, last_success_at=None,
            last_error_code=None, sync_interval_minutes=15,
        )

    db = MagicMock()
    db.commit = AsyncMock()
    with patch.object(api.lifecycle, "create_connector", fake_create):
        out = await _create(db, user, ws, ["o/a"], tid)
        assert saved["repos"] == ["o/A"]  # ticket spelling
        assert (out.installation_id, out.authorized_github_user_id) == (7, 4242)
        assert out.created_by == user.user_id
        with pytest.raises(ForbiddenError):  # replay
            await _create(db, user, ws, ["o/a"], tid)


# ── callback route: T-U7 no user token in output/logs ────────────────────────

@pytest.mark.unit
@pytest.mark.asyncio
async def test_callback_response_and_logs_have_no_user_token(redis) -> None:
    user, ws = _user(), uuid.uuid4()
    state = await connect_ticket.issue_state(
        client_secret=SECRET, workspace_id=ws, user_id=user.user_id
    )
    settings = SimpleNamespace(github_app_client_id="cid", github_app_client_secret=SECRET)
    transport = _github_transport(installs=[7], repos_by_install={7: ["o/A", "o/B"]})
    real_client = httpx.AsyncClient

    def client_factory(*a, **k):
        return real_client(transport=transport)

    body = api.GitHubCallbackRequest(code="good", state=state)
    with (
        patch.object(api, "get_settings", return_value=settings),
        patch.object(api, "require_workspace_permission", AsyncMock()),
        patch.object(connect_flow.httpx, "AsyncClient", client_factory),
        structlog.testing.capture_logs() as captured,
    ):
        response = await api.github_callback(body, MagicMock(), user)
    blob = response.model_dump_json() + json.dumps(captured, default=str) + json.dumps(redis.data)
    assert USER_TOKEN not in blob and SECRET not in blob
    assert [r.full_name for r in response.repositories] == ["o/A", "o/B"]
    assert response.workspace_id == ws
