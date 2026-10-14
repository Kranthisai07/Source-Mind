"""Read-only MCP interface: real PostgreSQL (RLS) + real MCP client over HTTP.

Everything except the identity provider and the embedding model is real: the SDK server and its
OAuth resource-server middleware, the SourceMind verifier (a locally generated RSA key stands in
for the provider's signing key, served through the same JWKS code path), the database under
FORCE RLS as the restricted runtime role, and the official `mcp` client in both protocol eras.

SIMULATED (not proof of the live integration): Clerk (no live issuer, no audience-binding
evidence), embeddings, Claude Code.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import httpx2
import pytest
import pytest_asyncio
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from jose import jwt as jose_jwt
from jose.utils import long_to_base64
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.mcp import server as mcp_server
from sourcemind.mcp.settings import McpSettings
from sourcemind.models.connector import ArtifactLink
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = pytest.mark.asyncio

ISSUER = "https://issuer.mcp-test.example"
KID = "test-key-1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Keys:
    def __init__(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        nums = key.public_key().public_numbers()
        self.jwk = {
            "kty": "RSA", "kid": KID, "alg": "RS256", "use": "sig",
            "n": long_to_base64(nums.n).decode(), "e": long_to_base64(nums.e).decode(),
        }

    def mint(self, *, sub: str, aud: str, iss: str = ISSUER, exp_in: int = 600,
             kid: str = KID, pem: str | None = None, extra: dict | None = None) -> str:
        now = int(time.time())
        claims = {"iss": iss, "sub": sub, "aud": aud, "iat": now, "exp": now + exp_in,
                  "client_id": "test-client", "scope": "memories:read", **(extra or {})}
        return jose_jwt.encode(claims, pem or self.pem, algorithm="RS256", headers={"kid": kid})


KEYS = _Keys()
OTHER_KEYS = _Keys()


class Fixture:
    def __init__(self, base: str, data: SecurityTenantData, ids: dict[str, uuid.UUID],
                 clerk: dict[str, str]) -> None:
        self.base = base
        self.url = f"{base}/mcp"
        self.data = data
        self.ids = ids
        self.clerk = clerk  # label -> clerk_id


@pytest_asyncio.fixture
async def mcp_env(supabase_engine: AsyncEngine, supabase_url: str):
    data = await _seed_security_tenants(supabase_engine)
    ids: dict[str, uuid.UUID] = {}
    async with AsyncSession(supabase_engine, expire_on_commit=False) as s:
        await set_rls_user_context(s, data.owner_id)

        async def add(ws: uuid.UUID, label: str, text_: str, *, stale: bool = False) -> None:
            await set_rls_workspace_context(s, ws)
            doc = Document(
                workspace_id=ws, submitter_id=data.owner_id, source_type="text",
                sha256_hash=hashlib.sha256((label + uuid.uuid4().hex).encode()).hexdigest(),
                ingestion_status="completed",
            )
            s.add(doc)
            await s.flush()
            mem = Memory(
                workspace_id=ws, document_id=doc.id, content=text_,
                content_hash=hashlib.sha256((text_ + label).encode()).hexdigest(),
                **({"source_stale_at": datetime.now(UTC), "source_stale_reason": "source_revised"}
                   if stale else {}),
            )
            s.add(mem)
            await s.flush()
            s.add(ArtifactLink(
                workspace_id=ws, document_id=doc.id, memory_id=mem.id, source_tool="github",
                source_type="pull_request", source_id=f"o/r/pull/{len(ids) + 1}",
                source_url=f"https://github.test/o/r/pull/{len(ids) + 1}", source_author="octocat",
                source_revision="rev-1", revision_seq=1,
            ))
            await s.flush()  # flush under THIS workspace context before the next switch
            ids[label] = mem.id

        await add(data.target_workspace_id, "target", "We cache sessions in Redis for speed.")
        await add(data.target_workspace_id, "stale", "Edited note about Redis caching.", stale=True)
        await add(data.other_workspace_id, "other", "Other workspace secret about Redis caching.")
        await s.commit()
        clerk = {
            "member": f"member-{data.clerk_ids[data.member_id].split('-', 1)[1]}",
        }
    # Clerk ids as stored by the seed helper.
    clerk_ids = {
        "member": data.clerk_ids[data.member_id],
        "viewer": data.clerk_ids[data.viewer_id],
        "outsider": data.clerk_ids[data.other_workspace_member_id],
        "zero": data.clerk_ids[data.zero_membership_id],
    }
    del clerk

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    cfg = McpSettings(
        enabled=True, resource_url=f"{base}/mcp", issuer_url=ISSUER,
        allowed_hosts=[f"127.0.0.1:{port}"], allowed_origins=[],
    )
    # The server runs in its own thread and event loop; its engine must be created there.
    holder: dict[str, async_sessionmaker] = {}

    def factory_provider():
        if "f" not in holder:
            engine = create_async_engine(supabase_url, pool_size=2, max_overflow=0)
            holder["f"] = async_sessionmaker(engine, expire_on_commit=False)
        return holder["f"]


    async def fake_embedding(query: str, client: object | None):
        return [0.0] * 3072, True

    async def fake_jwks(url: str, *, force_refresh: bool = False):
        return [KEYS.jwk]

    mcp = mcp_server.build_mcp_server(cfg)
    app_sub = mcp.streamable_http_app(
        transport_security=mcp_server.TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=cfg.effective_allowed_hosts(),
            allowed_origins=[],
        )
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/", app_sub)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)

    patches = [
        patch.object(mcp_server, "get_mcp_settings", return_value=cfg),
        patch.object(mcp_server, "get_session_factory", new=factory_provider),
        patch("sourcemind.mcp.auth._fetch_jwks", new=fake_jwks),
        patch("sourcemind.services.search.hybrid._get_query_embedding", new=fake_embedding),
        patch("sourcemind.services.memory.read.enforce_rate_limit", new=AsyncMock()),
        patch("sourcemind.mcp.service.enforce_rate_limit", new=AsyncMock()),
    ]
    for p in patches:
        p.start()
    thread.start()
    for _ in range(100):
        try:
            httpx.get(f"{base}/.well-known/oauth-protected-resource/mcp", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    try:
        yield Fixture(base, data, ids, clerk_ids)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for p in patches:
            p.stop()


def _client(fx: Fixture, token: str | None, mode: str) -> Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    hc = httpx2.AsyncClient(headers=headers, timeout=30)
    return Client(streamable_http_client(fx.url, http_client=hc), mode=mode)


def _code(result) -> str:
    """Stable error code of a failed tool call (the SDK prefixes 'Error executing tool X: ')."""
    assert result.is_error is True
    text_ = result.content[0].text
    return text_.split(": ", 2)[1] if text_.startswith("Error executing tool") else text_


def _payload(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


MODES = ["2026-07-28", "legacy"]


@pytest.mark.parametrize("mode", MODES)
async def test_real_client_lists_two_read_only_tools_and_searches_with_citations(
    mcp_env: Fixture, mode: str
) -> None:
    fx = mcp_env
    tok = KEYS.mint(sub=fx.clerk["member"], aud=fx.url)
    async with _client(fx, tok, mode) as c:
        tools = await c.list_tools()
        names = sorted(t.name for t in tools.tools)
        assert names == ["get_memory", "search_memories"]  # nothing that writes
        assert all(t.annotations and t.annotations.read_only_hint for t in tools.tools)

        res = await c.call_tool(
            "search_memories",
            {"query": "Redis caching", "workspace_id": str(fx.data.target_workspace_id),
             "mode": "keyword"},
        )
        out = _payload(res)
        ids = {r["memory_id"] for r in out["results"]}
        assert str(fx.ids["target"]) in ids and str(fx.ids["stale"]) in ids
        assert str(fx.ids["other"]) not in ids  # other workspace never appears
        hit = next(r for r in out["results"] if r["memory_id"] == str(fx.ids["target"]))
        assert hit["source"]["url"].startswith("https://github.test/o/r/pull/")
        assert hit["authorship"]["status"] == "unresolved"
        assert hit["citation"] == f"sourcemind://memory/{fx.ids['target']}"
        assert hit["content_is_untrusted"] is True and "untrusted" in out["notice"].lower()
        stale = next(r for r in out["results"] if r["memory_id"] == str(fx.ids["stale"]))
        assert stale["source_state"]["status"] == "stale"
        assert stale["source_state"]["reason"] == "source_revised"


@pytest.mark.parametrize("mode", MODES)
async def test_get_memory_and_cross_workspace_denial(mcp_env: Fixture, mode: str) -> None:
    fx = mcp_env
    tok = KEYS.mint(sub=fx.clerk["member"], aud=fx.url)
    async with _client(fx, tok, mode) as c:
        ok = _payload(await c.call_tool("get_memory", {"memory_id": str(fx.ids["target"])}))
        assert ok["memory"]["memory_id"] == str(fx.ids["target"])
        assert ok["memory"]["source"]["id"].startswith("o/r/pull/")
        assert ok["version_history"] and ok["is_current"] is True

        # Another workspace's memory, a missing id and a mismatched selector give ONE outcome.
        outcomes = []
        for args in (
            {"memory_id": str(fx.ids["other"])},
            {"memory_id": str(uuid.uuid4())},
            {"memory_id": str(fx.ids["target"]), "workspace_id": str(fx.data.other_workspace_id)},
        ):
            r = await c.call_tool("get_memory", args)
            outcomes.append((_code(r), r.content[0].text))
        assert len(set(outcomes)) == 1 and outcomes[0][0] == "not_found"

        # Searching a workspace the user is not in is the same not_found.
        r = await c.call_tool(
            "search_memories",
            {"query": "Redis", "workspace_id": str(fx.data.other_workspace_id)},
        )
        assert _code(r) == "not_found"


async def test_unknown_account_and_non_member_cannot_read(mcp_env: Fixture) -> None:
    fx = mcp_env
    # A valid token for a user SourceMind has never seen: refused, not provisioned.
    async with _client(fx, KEYS.mint(sub="user_never_seen", aud=fx.url), "2026-07-28") as c:
        r = await c.call_tool("search_memories", {"query": "Redis"})
        assert _code(r) == "unauthorized"
    # A user with no membership anywhere sees nothing.
    async with _client(fx, KEYS.mint(sub=fx.clerk["zero"], aud=fx.url), "2026-07-28") as c:
        r = await c.call_tool("search_memories", {"query": "Redis"})
        assert _code(r) == "not_found"
    # A member of the OTHER workspace cannot name the target workspace.
    async with _client(fx, KEYS.mint(sub=fx.clerk["outsider"], aud=fx.url), "2026-07-28") as c:
        r = await c.call_tool(
            "search_memories",
            {"query": "Redis", "workspace_id": str(fx.data.target_workspace_id)},
        )
        assert _code(r) == "not_found"


async def test_membership_removal_takes_effect_on_the_next_call(
    mcp_env: Fixture, supabase_engine: AsyncEngine
) -> None:
    fx = mcp_env
    tok = KEYS.mint(sub=fx.clerk["viewer"], aud=fx.url)  # viewers may read
    async with _client(fx, tok, "2026-07-28") as c:
        first = await c.call_tool("get_memory", {"memory_id": str(fx.ids["target"])})
        assert first.is_error is False
        async with AsyncSession(supabase_engine) as s:
            await set_rls_user_context(s, fx.data.owner_id)
            await set_rls_workspace_context(s, fx.data.target_workspace_id)
            from sqlalchemy import text
            await s.execute(
                text("UPDATE workspace_members SET status='departed', departed_at=now() "
                     "WHERE workspace_id=:w AND user_id=:u"),
                {"w": fx.data.target_workspace_id, "u": fx.data.viewer_id},
            )
            await s.commit()
        again = await c.call_tool("get_memory", {"memory_id": str(fx.ids["target"])})
        assert _code(again) == "not_found"


async def test_invalid_arguments_return_stable_error_codes(mcp_env: Fixture) -> None:
    fx = mcp_env
    tok = KEYS.mint(sub=fx.clerk["member"], aud=fx.url)
    async with _client(fx, tok, "2026-07-28") as c:
        for tool, args, code in (
            ("get_memory", {"memory_id": "not-a-uuid"}, "invalid_argument"),
            ("search_memories", {"query": "x", "mode": "fuzzy"}, "invalid_argument"),
            ("search_memories", {"query": "x", "workspace_id": "nope"}, "invalid_argument"),
        ):
            r = await c.call_tool(tool, args)
            assert _code(r) == code, (tool, args)


async def test_unexpected_failure_reaches_the_client_as_a_fixed_internal_error(
    mcp_env: Fixture,
) -> None:
    """Exception text (SQL, parameters, tenant data) must never be echoed to the client."""
    fx = mcp_env
    tok = KEYS.mint(sub=fx.clerk["member"], aud=fx.url)
    leak = "SELECT secret FROM users WHERE email = 'victim@example.test'"
    with patch(
        "sourcemind.mcp.service.search_memories", AsyncMock(side_effect=RuntimeError(leak))
    ):
        async with _client(fx, tok, "2026-07-28") as c:
            r = await c.call_tool("search_memories", {"query": "anything"})
    assert r.is_error is True
    text_ = r.content[0].text
    assert "internal_error" in text_
    assert "victim@example.test" not in text_ and "SELECT" not in text_


# ── authentication and transport (raw HTTP; these are the spec's MUST rules) ──


def _post(fx: Fixture, token: str | None, headers: dict | None = None) -> httpx.Response:
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return httpx.post(fx.url, json=body, headers=h, timeout=10)


async def test_token_rules_wrong_audience_issuer_signature_expiry_are_all_401(
    mcp_env: Fixture,
) -> None:
    fx = mcp_env
    bad = {
        "none": None,
        "garbage": "not.a.jwt",
        "wrong audience": KEYS.mint(sub=fx.clerk["member"], aud="https://other.example/mcp"),
        "audience is the API root, not the MCP resource": KEYS.mint(
            sub=fx.clerk["member"], aud=fx.base
        ),
        "wrong issuer": KEYS.mint(sub=fx.clerk["member"], aud=fx.url, iss="https://evil.example"),
        "expired": KEYS.mint(sub=fx.clerk["member"], aud=fx.url, exp_in=-60),
        "signed by another key": OTHER_KEYS.mint(
            sub=fx.clerk["member"], aud=fx.url, pem=OTHER_KEYS.pem
        ),
        "unknown kid": KEYS.mint(sub=fx.clerk["member"], aud=fx.url, kid="nope"),
        "audience-bound but no scope": KEYS.mint(
            sub=fx.clerk["member"], aud=fx.url, extra={"scope": ""}
        ),
        "audience-bound but only unrelated scopes": KEYS.mint(
            sub=fx.clerk["member"], aud=fx.url, extra={"scope": "openid email profile"}
        ),
    }
    bodies = set()
    for label, token in bad.items():
        r = _post(fx, token, {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"})
        assert r.status_code == 401, (label, r.status_code)
        assert "resource_metadata" in r.headers.get("www-authenticate", ""), label
        bodies.add(r.text)
    assert len(bodies) == 1, "failures must not reveal which check failed"



async def test_get_memory_has_its_own_rate_limit(mcp_env: Fixture) -> None:
    from sourcemind.core.exceptions import RateLimitExceededError

    fx = mcp_env
    tok = KEYS.mint(sub=fx.clerk["member"], aud=fx.url)
    with patch(
        "sourcemind.mcp.service.enforce_rate_limit",
        new=AsyncMock(side_effect=RateLimitExceededError("limit", retry_after_seconds=5)),
    ) as limiter:
        async with _client(fx, tok, "2026-07-28") as c:
            r = await c.call_tool("get_memory", {"memory_id": str(fx.ids["target"])})
    assert _code(r) == "rate_limited"
    assert limiter.await_args.args[0].value == "mcp-read"  # not the interactive search budget


async def test_protected_resource_metadata_names_this_resource(mcp_env: Fixture) -> None:
    fx = mcp_env
    r = httpx.get(f"{fx.base}/.well-known/oauth-protected-resource/mcp", timeout=5)
    assert r.status_code == 200
    doc = r.json()
    assert doc["resource"] == fx.url
    assert [u.rstrip("/") for u in doc["authorization_servers"]] == [ISSUER]


async def test_origin_host_and_method_rules(mcp_env: Fixture) -> None:
    fx = mcp_env
    tok = KEYS.mint(sub=fx.clerk["member"], aud=fx.url)
    ok_headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/list"}
    evil = _post(fx, tok, {**ok_headers, "Origin": "https://evil.example"})
    assert evil.status_code == 403
    bad_host = _post(fx, tok, {**ok_headers, "Host": "evil.example"})
    assert bad_host.status_code in {403, 421}
    for method in ("GET", "DELETE"):
        r = httpx.request(
            method, fx.url, headers={"Authorization": f"Bearer {tok}",
                                     "MCP-Protocol-Version": "2026-07-28"}, timeout=10
        )
        assert r.status_code == 405, method


async def test_search_payload_is_bounded(mcp_env: Fixture, supabase_engine: AsyncEngine) -> None:
    fx = mcp_env
    big = "Redis " + ("long text " * 5000)
    async with AsyncSession(supabase_engine, expire_on_commit=False) as s:
        await set_rls_user_context(s, fx.data.owner_id)
        await set_rls_workspace_context(s, fx.data.target_workspace_id)
        s.add(Memory(workspace_id=fx.data.target_workspace_id, content=big,
                     content_hash=hashlib.sha256(big.encode()).hexdigest()))
        await s.commit()
    tok = KEYS.mint(sub=fx.clerk["member"], aud=fx.url)
    async with _client(fx, tok, "2026-07-28") as c:
        r = await c.call_tool(
            "search_memories",
            {"query": "Redis", "workspace_id": str(fx.data.target_workspace_id),
             "mode": "keyword", "limit": 10},
        )
        out = _payload(r)
        assert len(out["results"]) <= 10
        assert all(len(x["content"]) <= 1500 for x in out["results"])
        assert any(x["content_truncated"] for x in out["results"])
        assert len(json.dumps(out)) < 40_000
        too_many = await c.call_tool("search_memories", {"query": "Redis", "limit": 11})
        assert too_many.is_error is True
