"""
Unit tests for workspace API endpoints.

Uses FastAPI dependency_overrides to inject mocked DB and auth.
No real database required.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from sourcemind.core.config import get_settings
from sourcemind.core.dependencies import AuthenticatedUser


# ── Constants ─────────────────────────────────────────────────────────────────

MOCK_USER_ID  = UUID("00000000-0000-4000-8000-000000000001")
MOCK_WS_ID    = UUID("00000000-0000-4000-8000-000000000010")
MOCK_ORG_ID   = UUID("00000000-0000-4000-8000-000000000020")
_NOW = datetime(2026, 4, 12, tzinfo=timezone.utc)
_IDEM_KEY = "550e8400-e29b-41d4-a716-446655440000"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=MOCK_USER_ID,
        clerk_id="user_test123",
        email="test@example.com",
        display_name="Test User",
    )


def _mock_workspace(
    id: UUID = MOCK_WS_ID,
    name: str = "Engineering",
    slug: str = "engineering",
) -> MagicMock:
    ws = MagicMock()
    ws.id = id
    ws.organization_id = MOCK_ORG_ID
    ws.name = name
    ws.slug = slug
    ws.description = None
    ws.created_at = _NOW
    ws.updated_at = None
    ws.deleted_at = None
    ws.member_count = None
    return ws


def _make_app(mock_session: AsyncMock):
    """Build app with dependency_overrides for DB and auth."""
    with patch("sourcemind.main.init_db", new_callable=AsyncMock), \
         patch("sourcemind.main.init_redis", new_callable=AsyncMock), \
         patch("sourcemind.main._init_neo4j", new_callable=AsyncMock), \
         patch("sourcemind.main._init_kafka", new_callable=AsyncMock), \
         patch("sourcemind.main._close_neo4j", new_callable=AsyncMock), \
         patch("sourcemind.main._close_kafka", new_callable=AsyncMock), \
         patch("sourcemind.main.close_redis", new_callable=AsyncMock), \
         patch("sourcemind.main.close_db", new_callable=AsyncMock):
        from sourcemind.main import create_app
        from sourcemind.core.database import get_db_session
        from sourcemind.core.dependencies import get_current_user

        app = create_app()

        async def _override_db():
            yield mock_session

        async def _override_user():
            return _mock_user()

        app.dependency_overrides[get_db_session] = _override_db
        app.dependency_overrides[get_current_user] = _override_user
        return app


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── GET /v1/workspaces ────────────────────────────────────────────────────────

@pytest.mark.unit
class TestListWorkspaces:
    def _session_returning(self, workspaces: list) -> AsyncMock:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = workspaces
        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        return session

    def test_returns_200_with_workspace_list(self):
        ws = _mock_workspace()
        app = _make_app(self._session_returning([ws]))
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/v1/workspaces")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1
        assert body["data"][0]["slug"] == "engineering"

    def test_returns_empty_list_when_no_memberships(self):
        app = _make_app(self._session_returning([]))
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/v1/workspaces")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_response_contains_meta(self):
        app = _make_app(self._session_returning([]))
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/v1/workspaces")
        body = resp.json()
        assert "meta" in body
        assert "request_id" in body["meta"]
        assert "latency_ms" in body["meta"]

    def test_without_auth_override_returns_401_or_500(self):
        """With no auth dependency override, request should fail auth."""
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
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/v1/workspaces")
        assert resp.status_code in (401, 422, 500)

    def test_multiple_workspaces_all_returned(self):
        ws1 = _mock_workspace(name="Team A", slug="team-a")
        ws2 = _mock_workspace(
            id=UUID("00000000-0000-4000-8000-000000000011"),
            name="Team B",
            slug="team-b",
        )
        app = _make_app(self._session_returning([ws1, ws2]))
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/v1/workspaces")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2


# ── POST /v1/workspaces ───────────────────────────────────────────────────────

@pytest.mark.unit
class TestCreateWorkspace:
    def _session_for_create(self, existing_org=None) -> AsyncMock:
        """Session where org lookup returns existing_org (or None for auto-create)."""
        org_result = MagicMock()
        org_result.scalar_one_or_none.return_value = existing_org

        slug_result = MagicMock()
        slug_result.scalar_one_or_none.return_value = None  # no slug collision

        session = AsyncMock()
        # Return org_result first call, slug_result second, None for rest
        session.execute = AsyncMock(side_effect=[org_result, slug_result])
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        async def _refresh(obj):
            obj.id = MOCK_WS_ID
            obj.organization_id = MOCK_ORG_ID
            obj.name = getattr(obj, "name", "My Team")
            obj.slug = getattr(obj, "slug", "my-team")
            obj.description = None
            obj.created_at = _NOW
            obj.updated_at = None

        session.refresh = AsyncMock(side_effect=_refresh)
        return session

    def test_returns_400_without_idempotency_key(self):
        # App raises InvalidIdempotencyKeyError (SM013) → 400 Bad Request
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"name": "My Team", "slug": "my-team"},
            )
        assert resp.status_code == 400

    def test_returns_422_on_invalid_slug_with_spaces(self):
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"name": "My Team", "slug": "my team"},
                headers={"Idempotency-Key": _IDEM_KEY},
            )
        assert resp.status_code == 422

    def test_returns_422_on_uppercase_slug(self):
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"name": "My Team", "slug": "MyTeam"},
                headers={"Idempotency-Key": _IDEM_KEY},
            )
        assert resp.status_code == 422

    def test_returns_422_on_leading_hyphen_slug(self):
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"name": "My Team", "slug": "-bad"},
                headers={"Idempotency-Key": _IDEM_KEY},
            )
        assert resp.status_code == 422

    def test_returns_422_on_trailing_hyphen_slug(self):
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"name": "My Team", "slug": "bad-"},
                headers={"Idempotency-Key": _IDEM_KEY},
            )
        assert resp.status_code == 422

    def test_valid_request_returns_201(self):
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"name": "My Team", "slug": "my-team"},
                headers={"Idempotency-Key": _IDEM_KEY},
            )
        assert resp.status_code == 201

    def test_valid_request_response_shape(self):
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"name": "My Team", "slug": "my-team"},
                headers={"Idempotency-Key": _IDEM_KEY},
            )
        body = resp.json()
        assert "data" in body
        assert body["data"]["slug"] == "my-team"
        assert body["data"]["name"] == "My Team"

    def test_missing_name_returns_422(self):
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"slug": "my-team"},
                headers={"Idempotency-Key": _IDEM_KEY},
            )
        assert resp.status_code == 422

    def test_missing_slug_returns_422(self):
        app = _make_app(self._session_for_create())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/v1/workspaces",
                json={"name": "My Team"},
                headers={"Idempotency-Key": _IDEM_KEY},
            )
        assert resp.status_code == 422


# ── DB integration tests (skipped without local PostgreSQL) ───────────────────

_pg_available = pytest.mark.skipif(
    True,
    reason="Requires local PostgreSQL (pg_ctl not found). Run when storage permits.",
)


@_pg_available
class TestWorkspaceDbIntegration:
    def test_create_and_list_workspace(self):
        pass

    def test_second_workspace_reuses_existing_org(self):
        pass

    def test_list_only_returns_members_workspaces(self):
        pass
