"""Test-process network guard: nothing leaves the machine unless it is explicitly allowed.

Installed for the whole pytest session (see ``conftest.py``). It does not depend on API keys, base URLs
or mocks: it sits on the socket layer, so an SDK client that was never mocked (the way a memory-edit
path once reached a model provider's embeddings endpoint with a placeholder key) is stopped before a
single byte is sent.

* Loopback (127.0.0.0/8, ::1, ``localhost``) and AF_UNIX are always allowed: the disposable
  PostgreSQL/Redis and in-process servers live there.
* Anything else raises ``ExternalNetworkBlocked`` AND is recorded in ``ATTEMPTS``. A per-test check in
  ``conftest.py`` fails the test if anything was recorded, so a library that swallows the exception
  (many catch ``OSError``) cannot hide an attempt.
* ``NETGUARD_ALLOW`` (comma separated ``host:port``, exact match, no wildcards) is empty in CI and in the
  default run; set it only for a deliberate live check.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading

ATTEMPTS: list[str] = []
_LOCK = threading.Lock()
_INSTALLED = False
_LOCAL_NAMES = {"localhost", "ip6-localhost"}

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


class ExternalNetworkBlocked(ConnectionRefusedError):
    """An outbound connection to a non-loopback, non-allowlisted destination was attempted."""


def _allowed_pairs() -> set[str]:
    raw = os.environ.get("NETGUARD_ALLOW", "")
    return {x.strip().lower() for x in raw.split(",") if x.strip()}


def _is_loopback(host: object) -> bool:
    if host in (None, "", b""):
        return True  # wildcard bind / passive socket, not an outbound destination
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).lower()
    if host in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _record(what: str) -> None:
    with _LOCK:
        ATTEMPTS.append(what)


def _check(host: object, port: object, how: str) -> None:
    if _is_loopback(host):
        return
    pair = f"{str(host).lower()}:{port}"
    if pair in _allowed_pairs():
        return
    _record(f"{how} {host}:{port}")
    raise ExternalNetworkBlocked(f"outbound network blocked by tests/netguard: {how} {host}:{port}")


def _guarded_connect(self: socket.socket, address: object):  # type: ignore[no-untyped-def]
    if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
        _check(address[0], address[1] if len(address) > 1 else None, "connect")
    return _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: object):  # type: ignore[no-untyped-def]
    if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple):
        _check(address[0], address[1] if len(address) > 1 else None, "connect_ex")
    return _real_connect_ex(self, address)


def _guarded_getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
    _check(host, port, "resolve")
    return _real_getaddrinfo(host, port, *args, **kwargs)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]
    _INSTALLED = True


def take_attempts() -> list[str]:
    """Return and clear the recorded attempts."""
    with _LOCK:
        taken = list(ATTEMPTS)
        ATTEMPTS.clear()
    return taken
