"""The test-process network guard: model providers and every other external host are unreachable.

These tests use realistic-looking keys and NO mocks on purpose: the guard has to work when nothing
else does. Each deliberately attempts a blocked connection, hence ``netguard_expected``.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from tests import netguard

pytestmark = [pytest.mark.unit, pytest.mark.netguard_expected]


@pytest.fixture(autouse=True)
def _clean():
    netguard.take_attempts()
    yield
    netguard.take_attempts()


def _fresh(cls):
    """The real SDK client class, immune to tests that patched ``cls.__new__`` and left a stale slot
    behind (CPython keeps forwarding keyword arguments to ``object.__new__`` after such a patch)."""

    class _Real(cls):  # type: ignore[misc, valid-type]
        def __new__(cls_, *_a, **_k):  # noqa: N804
            return object.__new__(cls_)

    return _Real


def _hosts() -> str:
    return " ".join(netguard.take_attempts())


async def test_openai_embeddings_call_is_blocked_before_any_byte_is_sent():
    from openai import APIConnectionError, AsyncOpenAI


    client = _fresh(AsyncOpenAI)(api_key="sk-proj-" + "a" * 48, max_retries=0)
    with pytest.raises(APIConnectionError):
        await client.embeddings.create(model="text-embedding-3-large", input="hello")
    assert "api.openai.com" in _hosts()


async def test_anthropic_call_is_blocked():
    from anthropic import APIConnectionError, AsyncAnthropic

    client = _fresh(AsyncAnthropic)(api_key="sk-ant-api03-" + "b" * 80, max_retries=0)
    with pytest.raises(APIConnectionError):
        await client.messages.create(
            model="claude-sonnet-5-5", max_tokens=8, messages=[{"role": "user", "content": "hi"}]
        )
    assert "api.anthropic.com" in _hosts()


async def test_plain_httpx_to_a_provider_host_is_blocked():
    async with httpx.AsyncClient() as c:
        with pytest.raises(httpx.ConnectError):
            await c.get("https://api.openai.com/v1/models")
    assert "api.openai.com" in _hosts()


def test_a_raw_ip_connection_is_blocked_without_name_resolution():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    with pytest.raises(netguard.ExternalNetworkBlocked):
        s.connect(("1.1.1.1", 443))
    s.close()
    assert "1.1.1.1:443" in _hosts()


def test_a_swallowed_error_still_leaves_a_record():
    """The verifier and many clients catch OSError; the audit list is what the per-test check reads."""
    try:
        socket.getaddrinfo("api.github.com", 443)
    except OSError:
        pass
    assert "resolve api.github.com:443" in _hosts()


def test_loopback_and_unix_sockets_still_work():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    c.connect(srv.getsockname())
    c.close()
    srv.close()
    a, b = socket.socketpair()
    a.close()
    b.close()
    assert netguard.take_attempts() == []


def test_allowlist_is_exact_host_and_port_with_no_wildcards(monkeypatch):
    monkeypatch.setenv("NETGUARD_ALLOW", "api.github.com:443")
    netguard._check("api.github.com", 443, "connect")  # exact: allowed
    for host, port in (("api.github.com", 80), ("evil.api.github.com", 443), ("github.com", 443)):
        with pytest.raises(netguard.ExternalNetworkBlocked):
            netguard._check(host, port, "connect")
    monkeypatch.setenv("NETGUARD_ALLOW", "*.github.com:443")
    with pytest.raises(netguard.ExternalNetworkBlocked):
        netguard._check("api.github.com", 443, "connect")  # a wildcard entry matches nothing


