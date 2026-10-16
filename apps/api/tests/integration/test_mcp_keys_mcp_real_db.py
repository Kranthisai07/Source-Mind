"""MCP API-key authentication end to end: real PostgreSQL (FORCE RLS, restricted runtime role),
the production ``mount_mcp`` in ``api_key`` mode, and the official ``mcp`` client over HTTP.

Everything except the embedding model and the rate-limit Redis is real. SIMULATED: embeddings,
rate limiter (patched to a no-op here; the limiter itself has its own tests), Claude Code/Cursor/
Codex (NOT exercised by this file; see the live-verification step).
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import httpx
import httpx2
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.mcp import server as mcp_server
from sourcemind.mcp.settings import McpSettings
from sourcemind.services import mcp_keys
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = pytest.mark.asyncio


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Env:
    url: str
    base: str
    data: SecurityTenantData
    engine: AsyncEngine


async def _create_key(engine: AsyncEngine, data: SecurityTenantData, user_id: uuid.UUID,
                      workspace_id: uuid.UUID) -> mcp_keys.CreatedKey:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await set_rls_user_context(s, user_id)
        await set_rls_workspace_context(s, workspace_id)
        created = await mcp_keys.create_key(
            s, user_id=user_id, workspace_id=workspace_id, name="test", expires_in_days=90
        )
        await s.commit()
        return created


async def _revoke(engine: AsyncEngine, actor: uuid.UUID, workspace_id: uuid.UUID,
                  key_id: uuid.UUID) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await set_rls_user_context(s, actor)
        await set_rls_workspace_context(s, workspace_id)
        await mcp_keys.revoke_key(
            s, key_id=key_id, actor_user_id=actor, workspace_id=workspace_id, is_admin=True
        )
        await s.commit()


@pytest_asyncio.fixture
async def env(supabase_engine: AsyncEngine, supabase_url: str):
    data = await _seed_security_tenants(supabase_engine)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    cfg = McpSettings(
        enabled=True, auth_mode="api_key", resource_url=f"{base}/mcp",
        allowed_hosts=[f"127.0.0.1:{port}"], allowed_origins=[],
    )
    holder: dict[str, async_sessionmaker] = {}

    def factory_provider():
        # The server runs in its own thread and event loop; its engine must be created there.
        if "f" not in holder:
            engine = create_async_engine(supabase_url, pool_size=2, max_overflow=0)
            holder["f"] = async_sessionmaker(engine, expire_on_commit=False)
        return holder["f"]

    async def fake_embedding(query: str, client: object | None):
        return [0.0] * 3072, True

    patches = [
        patch.object(mcp_server, "get_mcp_settings", return_value=cfg),
        patch.object(mcp_server, "get_session_factory", new=factory_provider),
        patch("sourcemind.services.search.hybrid._get_query_embedding", new=fake_embedding),
        patch("sourcemind.services.memory.read.enforce_rate_limit", new=AsyncMock()),
        patch("sourcemind.mcp.service.enforce_rate_limit", new=AsyncMock()),
    ]
    for p in patches:
        p.start()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_server.mcp_lifespan(app):
            yield

    app = FastAPI(lifespan=lifespan)
    assert mcp_server.mount_mcp(app) is True  # the PRODUCTION mount, in api_key mode
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            httpx.get(f"{base}/nothing", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    try:
        yield Env(f"{base}/mcp", base, data, supabase_engine)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for p in patches:
            p.stop()


def _client(url: str, key: str, mode: str = "2026-07-28") -> Client:
    hc = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30)
    return Client(streamable_http_client(url, http_client=hc), mode=mode)


def _code(result) -> str:
    assert result.is_error is True
    body = result.content[0].text
    return body.split(": ", 2)[1] if body.startswith("Error executing tool") else body


def _post(url: str, headers: dict[str, str]) -> httpx.Response:
    h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    h.update(headers)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return httpx.post(url, json=body, headers=h, timeout=10)


@pytest.mark.parametrize("mode", ["2026-07-28", "legacy"])
async def test_valid_key_lists_two_read_only_tools_and_reads_its_workspace(
    env: Env, mode: str
) -> None:
    key = await _create_key(
        env.engine, env.data, env.data.member_id, env.data.target_workspace_id
    )
    async with _client(env.url, key.key, mode) as c:
        tools = await c.list_tools()
        assert sorted(t.name for t in tools.tools) == ["get_memory", "search_memories"]
        got = await c.call_tool("get_memory", {"memory_id": str(env.data.target_memory_id)})
        assert got.is_error is False
        found = await c.call_tool("search_memories", {"query": "tenant secret", "mode": "keyword"})
        assert found.is_error is False


async def test_key_is_bound_to_one_workspace_even_if_its_creator_belongs_to_two(
    env: Env,
) -> None:
    # The owner is a member of BOTH seeded workspaces; the key is bound to the target one.
    key = await _create_key(env.engine, env.data, env.data.owner_id, env.data.target_workspace_id)
    async with _client(env.url, key.key) as c:
        ok = await c.call_tool("get_memory", {"memory_id": str(env.data.target_memory_id)})
        assert ok.is_error is False
        other = await c.call_tool("get_memory", {"memory_id": str(env.data.other_memory_id)})
        assert _code(other) == "not_found"
        forced = await c.call_tool(
            "get_memory",
            {"memory_id": str(env.data.target_memory_id),
             "workspace_id": str(env.data.other_workspace_id)},
        )
        assert _code(forced) == "not_found"
        searched = await c.call_tool(
            "search_memories",
            {"query": "secret", "workspace_id": str(env.data.other_workspace_id)},
        )
        assert _code(searched) == "not_found"


async def test_revocation_takes_effect_on_the_next_call_of_a_connected_client(
    env: Env,
) -> None:
    key = await _create_key(env.engine, env.data, env.data.member_id, env.data.target_workspace_id)
    async with _client(env.url, key.key) as c:
        first = await c.call_tool("get_memory", {"memory_id": str(env.data.target_memory_id)})
        assert first.is_error is False
        await _revoke(env.engine, env.data.admin_id, env.data.target_workspace_id, key.id)
        with pytest.raises(Exception):  # noqa: B017, PT011 - transport rejects with HTTP 401
            await c.call_tool("get_memory", {"memory_id": str(env.data.target_memory_id)})
    assert _post(env.url, {"Authorization": f"Bearer {key.key}"}).status_code == 401


async def test_creator_losing_membership_stops_the_key_on_the_next_call(
    env: Env,
) -> None:
    key = await _create_key(env.engine, env.data, env.data.viewer_id, env.data.target_workspace_id)
    async with _client(env.url, key.key) as c:
        assert (
            await c.call_tool("get_memory", {"memory_id": str(env.data.target_memory_id)})
        ).is_error is False
        async with AsyncSession(env.engine) as s:
            await set_rls_user_context(s, env.data.owner_id)
            await set_rls_workspace_context(s, env.data.target_workspace_id)
            await s.execute(
                text("UPDATE workspace_members SET status='departed', departed_at=now() "
                     "WHERE workspace_id=:w AND user_id=:u"),
                {"w": env.data.target_workspace_id, "u": env.data.viewer_id},
            )
            await s.commit()
        again = await c.call_tool("get_memory", {"memory_id": str(env.data.target_memory_id)})
        assert _code(again) == "not_found"


async def test_every_bad_credential_is_the_same_401_and_nothing_is_advertised(
    env: Env,
) -> None:
    good = await _create_key(env.engine, env.data, env.data.member_id, env.data.target_workspace_id)
    revoked = await _create_key(
        env.engine, env.data, env.data.member_id, env.data.target_workspace_id
    )
    await _revoke(env.engine, env.data.admin_id, env.data.target_workspace_id, revoked.id)
    unknown = "smk_" + "A" * 43
    bad_headers = [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
        {"Authorization": f"Bearer {good.key}x"},
        {"Authorization": "Bearer smk_short"},
        {"Authorization": "Bearer " + "smk_" + "A" * 5000},
        {"Authorization": f"Bearer {unknown}"},
        {"Authorization": f"Bearer {revoked.key}"},
        {"Authorization": f"Token {good.key}"},
    ]
    seen = set()
    for headers in bad_headers:
        resp = _post(env.url, headers)
        assert resp.status_code == 401, headers
        assert resp.json() == {"error": "invalid_token"}
        assert "resource_metadata" not in resp.headers.get("www-authenticate", "")
        seen.add((resp.status_code, resp.text))
    assert len(seen) == 1  # indistinguishable
    # Two Authorization headers are refused even if one is valid.
    two = httpx.Request("POST", env.url, headers=[
        ("Authorization", f"Bearer {good.key}"), ("Authorization", f"Bearer {good.key}"),
        ("Accept", "application/json, text/event-stream"), ("Content-Type", "application/json"),
    ], json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    with httpx.Client(timeout=10) as hc:
        assert hc.send(two).status_code == 401
    # No OAuth authorization server is advertised in key mode.
    meta = httpx.get(f"{env.base}/.well-known/oauth-protected-resource/mcp", timeout=5)
    assert meta.status_code == 404


async def test_database_failure_is_a_503_not_a_credential_error(env: Env) -> None:
    key = await _create_key(env.engine, env.data, env.data.member_id, env.data.target_workspace_id)
    with patch(
        "sourcemind.mcp.keyauth.resolve_key", new=AsyncMock(side_effect=RuntimeError("db down"))
    ):
        resp = _post(env.url, {"Authorization": f"Bearer {key.key}"})
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "5"
    assert resp.json() == {"error": "temporarily_unavailable"}
    # The key itself is fine once the database is back: authentication passes (a bare tools/list
    # without an initialize is then a protocol-level 400, not an authentication error).
    after = _post(env.url, {"Authorization": f"Bearer {key.key}"})
    assert after.status_code not in {401, 503}
