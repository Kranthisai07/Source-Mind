"""API-key middleware: header parsing and request gating (no database)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from sourcemind.mcp.keyauth import (
    ApiKeyAuthMiddleware,
    KeyPrincipal,
    bearer_key_from_headers,
    current_key_principal,
)

KEY = "smk_" + "a" * 43
PRINCIPAL = KeyPrincipal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


def _h(*values: bytes) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", v) for v in values]


@pytest.mark.parametrize(
    "headers",
    [
        [],
        _h(b"Basic " + KEY.encode()),
        _h(b"Bearer"),
        _h(b"Bearer "),
        _h(b"Bearer " + KEY.encode() + b"x"),
        _h(b"Bearer smk_short"),
        _h(b"Bearer " + b"smk_" + b"A" * 5000),
        _h(b"Bearer " + KEY.encode(), b"Bearer " + KEY.encode()),
        _h(b"Bearer " + KEY.encode() + b" extra"),
        _h("Bearer smk_é".encode()),
        _h(b"Bearer " + b"clerk.jwt.value"),
    ],
)
def test_malformed_or_ambiguous_credentials_are_rejected(headers) -> None:
    assert bearer_key_from_headers(headers) is None


def test_well_formed_key_is_accepted_case_insensitive_scheme() -> None:
    assert bearer_key_from_headers(_h(b"Bearer " + KEY.encode())) == KEY
    assert bearer_key_from_headers(_h(b"bearer " + KEY.encode())) == KEY
    assert bearer_key_from_headers([(b"Authorization", b"Bearer " + KEY.encode())]) == KEY


async def _run(mw, path: str, headers) -> tuple[int | None, list[dict]]:
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request"}

    await mw({"type": "http", "path": path, "headers": headers}, receive, send)
    start = next((m for m in sent if m["type"] == "http.response.start"), None)
    return (start["status"] if start else None), sent


class _Spy:
    def __init__(self) -> None:
        self.called = False
        self.principal: KeyPrincipal | None = None

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        self.principal = current_key_principal.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def _mw(spy: _Spy) -> ApiKeyAuthMiddleware:
    return ApiKeyAuthMiddleware(spy, path="/mcp", session_factory=lambda: None)


async def test_rejected_requests_never_reach_the_app() -> None:
    spy = _Spy()
    status, sent = await _run(_mw(spy), "/mcp", [])
    assert status == 401 and spy.called is False
    assert b"resource_metadata" not in dict(sent[0]["headers"])[b"www-authenticate"]


async def test_unknown_key_is_401_and_valid_key_binds_identity_only_for_the_request() -> None:
    spy = _Spy()
    mw = _mw(spy)
    with patch("sourcemind.mcp.keyauth.authenticate_key", new=AsyncMock(return_value=None)):
        status, _ = await _run(mw, "/mcp", _h(b"Bearer " + KEY.encode()))
    assert status == 401 and spy.called is False
    with patch("sourcemind.mcp.keyauth.authenticate_key", new=AsyncMock(return_value=PRINCIPAL)):
        status, _ = await _run(mw, "/mcp/", _h(b"Bearer " + KEY.encode()))
    assert status == 200 and spy.principal == PRINCIPAL
    assert current_key_principal.get() is None  # reset after the request


async def test_lookup_failure_is_503_with_retry_after_not_401() -> None:
    spy = _Spy()
    with patch(
        "sourcemind.mcp.keyauth.authenticate_key", new=AsyncMock(side_effect=RuntimeError("x"))
    ):
        status, sent = await _run(_mw(spy), "/mcp", _h(b"Bearer " + KEY.encode()))
    assert status == 503 and spy.called is False
    assert dict(sent[0]["headers"])[b"retry-after"] == b"5"


async def test_other_paths_and_non_http_scopes_pass_through_untouched() -> None:
    spy = _Spy()
    status, _ = await _run(_mw(spy), "/health", [])
    assert status == 200 and spy.called is True and spy.principal is None
