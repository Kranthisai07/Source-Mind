"""MCP access policy: audience and scope are mandatory; client allowlisting is optional.

Policy (docs/architecture/MCP_INTERFACE.md): a token is accepted only if it was issued for THIS
resource (audience) AND carries the required scope (the user consented to reading memories), and
every call re-checks the caller's workspace membership. Restricting WHICH OAuth client may be used
is a separate, optional deployment choice and is off unless configured.
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from sourcemind.core import dependencies
from sourcemind.mcp import auth
from sourcemind.mcp.settings import McpSettings

RES = "https://api.example.test/mcp"


def _claims(**over):
    now = int(time.time())
    base = {"iss": "https://iss.test", "sub": "user_1", "aud": RES, "iat": now, "exp": now + 60,
            "client_id": "app-1", "scope": "memories:read"}
    base.update(over)
    return base


def _verifier(monkeypatch, claims, **kw):
    async def fake_verify(*_a, **_k):
        return claims

    monkeypatch.setattr(auth, "verify_access_jwt", fake_verify)
    return auth.ClerkAccessTokenVerifier(
        jwks_url="x", issuer="https://iss.test", resource_url=RES, **kw
    )


@pytest.mark.unit
async def test_required_scope_is_enforced(monkeypatch):
    v = _verifier(monkeypatch, _claims(scope="openid email"), required_scopes=["memories:read"])
    assert await v.verify_token("t") is None
    v = _verifier(monkeypatch, _claims(), required_scopes=["memories:read"])
    assert (await v.verify_token("t")) is not None


@pytest.mark.unit
async def test_client_allowlist_is_optional_and_only_narrows(monkeypatch):
    open_ = _verifier(monkeypatch, _claims(client_id="anything"), required_scopes=["memories:read"])
    assert await open_.verify_token("t") is not None  # no allowlist: any client with scope+aud
    pinned = _verifier(
        monkeypatch, _claims(client_id="rogue"), required_scopes=["memories:read"],
        allowed_client_ids=["app-1"],
    )
    assert await pinned.verify_token("t") is None


@pytest.mark.unit
def test_enabled_without_a_required_scope_is_refused():
    with pytest.raises(ValidationError):
        McpSettings(enabled=True, resource_url=RES, required_scopes=[])
    assert McpSettings(enabled=False).enabled is False  # off by default, nothing to validate
    assert McpSettings(enabled=True, resource_url=RES).required_scopes == ["memories:read"]


@pytest.mark.unit
async def test_unknown_key_id_cannot_force_an_outbound_fetch_per_request(monkeypatch):
    calls = {"n": 0}

    class Resp:
        def raise_for_status(self): ...
        def json(self): return {"keys": [{"kid": "k1"}]}

    class Client:
        def __init__(self, **_k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *_a): return False
        async def get(self, _url):
            calls["n"] += 1
            return Resp()

    monkeypatch.setattr(dependencies.httpx, "AsyncClient", Client)
    dependencies._jwks_cache.clear()
    await dependencies._fetch_jwks("https://j.test/jwks")  # first fill
    for _ in range(50):  # attacker-chosen unknown kids: each asks for a forced refresh
        await dependencies._fetch_jwks("https://j.test/jwks", force_refresh=True)
    assert calls["n"] == 1
    dependencies._jwks_cache["https://j.test/jwks"]["fetched_at"] -= 31  # interval elapsed
    await dependencies._fetch_jwks("https://j.test/jwks", force_refresh=True)
    assert calls["n"] == 2
