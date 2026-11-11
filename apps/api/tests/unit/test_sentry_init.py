"""Sentry wiring: reports unhandled API errors, and nothing that could carry user data."""

from __future__ import annotations

import json

import pytest
import sentry_sdk
from fastapi.testclient import TestClient
from sentry_sdk.transport import Transport

from sourcemind.core.config import Settings, get_settings
from sourcemind.core.exceptions import SourceMindError
from sourcemind.core.logging import configure_logging
from sourcemind.core.observability import init_sentry
from sourcemind.main import create_app


class _Collect(Transport):
    def __init__(self) -> None:
        super().__init__({})
        self.events: list[dict] = []

    def capture_envelope(self, envelope) -> None:  # type: ignore[no-untyped-def]
        for item in envelope.items:
            if item.headers.get("type") == "event":
                self.events.append(json.loads(item.payload.get_bytes()))


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("SENTRY_DSN", "https://key@o0.ingest.sentry.io/1")
    get_settings.cache_clear()
    yield
    sentry_sdk.get_client().close()
    get_settings.cache_clear()


def _app_with_routes(transport: _Collect, monkeypatch: pytest.MonkeyPatch):
    # create_app() initialises Sentry itself; hand it the fake transport.
    monkeypatch.setattr(
        "sourcemind.main.init_sentry", lambda s: init_sentry(s, transport=transport)
    )
    configure_logging()  # as in the lifespan: errors are also logged through stdlib logging
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    @app.get("/expected")
    async def expected() -> None:
        raise SourceMindError("expected")  # type: ignore[call-arg]

    return TestClient(app, raise_server_exceptions=False)


def test_no_dsn_is_a_noop() -> None:
    assert init_sentry(Settings(sentry_dsn="")) is False


def test_unhandled_error_is_reported_once_and_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Collect()
    client = _app_with_routes(transport, monkeypatch)
    resp = client.get(
        "/boom?token=secret-token",
        headers={"Authorization": "Bearer secret", "Cookie": "s=secret"},
    )
    sentry_sdk.flush()
    assert resp.status_code == 500 and resp.json()["error"]["code"] == "SM090"
    assert len(transport.events) == 1
    dumped = json.dumps(transport.events[0])
    assert "secret" not in dumped
    request = transport.events[0].get("request", {})
    assert "data" not in request and "cookies" not in request


def test_expected_errors_are_not_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _Collect()
    client = _app_with_routes(transport, monkeypatch)
    client.get("/expected")
    sentry_sdk.flush()
    assert transport.events == []
