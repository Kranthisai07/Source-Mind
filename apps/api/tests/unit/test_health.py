"""
Unit tests for the health check endpoint.

These tests mock all external dependencies to run without Docker.
Use `pytest -m integration` for tests that require running services.
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from sourcemind.core.config import get_settings


@pytest.fixture(autouse=True)
def reset_settings_cache() -> None:
    """Clear lru_cache between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    """
    Test client with mocked lifespan.

    We patch all startup/shutdown hooks to avoid requiring actual connections
    in unit tests. Integration tests handle real connections via testcontainers.
    """
    with patch("sourcemind.main.init_db", new_callable=AsyncMock), \
         patch("sourcemind.main.init_redis", new_callable=AsyncMock), \
         patch("sourcemind.main._init_neo4j", new_callable=AsyncMock), \
         patch("sourcemind.main._init_kafka", new_callable=AsyncMock), \
         patch("sourcemind.main._close_neo4j", new_callable=AsyncMock), \
         patch("sourcemind.main._close_kafka", new_callable=AsyncMock), \
         patch("sourcemind.main.close_redis", new_callable=AsyncMock), \
         patch("sourcemind.main.close_db", new_callable=AsyncMock):
        from sourcemind.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.mark.unit
def test_health_endpoint_exists(client: TestClient) -> None:
    """Health endpoint must be reachable at GET /health."""
    # We expect some response (even if DBs aren't connected)
    response = client.get("/health")
    assert response.status_code in (200, 503)


@pytest.mark.unit
def test_health_response_shape(client: TestClient) -> None:
    """Health response must contain required top-level fields."""
    response = client.get("/health")
    data = response.json()

    assert "status" in data
    assert data["status"] in ("healthy", "degraded", "unhealthy")
    assert "version" in data
    assert "environment" in data
    assert "components" in data


@pytest.mark.unit
def test_request_id_header_in_response(client: TestClient) -> None:
    """Every response must include X-Request-ID header."""
    response = client.get("/health")
    assert "X-Request-ID" in response.headers


@pytest.mark.unit
def test_custom_request_id_is_echoed(client: TestClient) -> None:
    """X-Request-ID from the client should be echoed back."""
    custom_id = "550e8400-e29b-41d4-a716-446655440000"
    response = client.get("/health", headers={"X-Request-ID": custom_id})
    assert response.headers.get("X-Request-ID") == custom_id


@pytest.mark.unit
def test_process_time_header_in_response(client: TestClient) -> None:
    """Every response must include X-Process-Time-Ms header."""
    response = client.get("/health")
    assert "X-Process-Time-Ms" in response.headers
    latency = float(response.headers["X-Process-Time-Ms"])
    assert latency >= 0


@pytest.mark.unit
def test_security_headers_present(client: TestClient) -> None:
    """Security headers must be present on every response."""
    response = client.get("/health")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"


@pytest.mark.unit
def test_404_returns_valid_response(client: TestClient) -> None:
    """Non-existent routes should return a 404 response."""
    response = client.get("/v1/nonexistent-endpoint")
    assert response.status_code == 404
