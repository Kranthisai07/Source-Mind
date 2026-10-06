"""Admin API for GitHub author links (D-021).

Links are ADMIN-ASSERTED: only workspace admins/owners manage them, the
target must be an active member, and every create/correct records who did it.
Corrections affect future syncs only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.core.dependencies import AuthenticatedUser, WorkspacePermission
from sourcemind.core.exceptions import (
    ValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceNotFoundError,
)

ROUTER = "sourcemind.api.v1.github_author_links"


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(user_id=uuid.uuid4(), clerk_id="c", email="a@example.com")


def _row(ws, gid, user, actor, *, updated=False):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=ws,
        github_user_id=gid,
        github_login="alice",
        user_id=user,
        created_by_user_id=actor,
        created_at=now,
        updated_by_user_id=actor if updated else None,
        updated_at=now if updated else None,
    )


def _db(*, member: bool = True, upsert_row=None, deleted: bool = True):
    statements: list[tuple[str, dict]] = []

    async def execute(stmt, params=None, **_k):
        sql = " ".join(str(stmt).split())
        statements.append((sql, dict(params or {})))
        r = MagicMock()
        if sql.startswith("SELECT 1 FROM workspace_members"):
            r.first.return_value = SimpleNamespace(ok=1) if member else None
        elif sql.startswith("INSERT INTO github_author_links"):
            r.first.return_value = upsert_row
        elif sql.startswith("DELETE FROM github_author_links"):
            r.first.return_value = SimpleNamespace(id=uuid.uuid4()) if deleted else None
        else:
            r.fetchall.return_value = []
        return r

    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.statements = statements
    return db


@pytest.mark.unit
async def test_put_creates_link_and_records_the_admin() -> None:
    from sourcemind.api.v1.github_author_links import GitHubAuthorLinkUpsert, put_github_author_link

    ws, target, admin = uuid.uuid4(), uuid.uuid4(), _admin()
    db = _db(upsert_row=_row(ws, 101, target, admin.user_id))
    require = AsyncMock(return_value="admin")
    with patch(f"{ROUTER}.require_workspace_permission", new=require):
        response = await put_github_author_link(
            workspace_id=ws,
            github_user_id=101,
            body=GitHubAuthorLinkUpsert(user_id=target, github_login="alice"),
            db=db,
            current_user=admin,
        )

    require.assert_awaited_once_with(db, admin.user_id, ws, WorkspacePermission.ADMINISTER)
    insert_sql, params = next(s for s in db.statements if s[0].startswith("INSERT"))
    assert "ON CONFLICT (workspace_id, github_user_id) DO UPDATE" in insert_sql
    assert params["actor"] == str(admin.user_id)
    assert params["uid"] == str(target)
    assert params["gid"] == 101
    assert response.user_id == target
    assert response.created_by_user_id == admin.user_id
    db.commit.assert_awaited_once()


@pytest.mark.unit
async def test_put_correction_stamps_updated_by() -> None:
    from sourcemind.api.v1.github_author_links import GitHubAuthorLinkUpsert, put_github_author_link

    ws, target, admin = uuid.uuid4(), uuid.uuid4(), _admin()
    db = _db(upsert_row=_row(ws, 101, target, admin.user_id, updated=True))
    with patch(f"{ROUTER}.require_workspace_permission", new=AsyncMock(return_value="owner")):
        response = await put_github_author_link(
            workspace_id=ws,
            github_user_id=101,
            body=GitHubAuthorLinkUpsert(user_id=target),
            db=db,
            current_user=admin,
        )

    insert_sql, _params = next(s for s in db.statements if s[0].startswith("INSERT"))
    assert "updated_by_user_id = CAST(:actor AS uuid)" in insert_sql
    assert "updated_at = now()" in insert_sql
    # A correction never rewrites who created the link.
    assert "created_by_user_id = " not in insert_sql.split("DO UPDATE")[1]
    assert response.updated_by_user_id == admin.user_id


@pytest.mark.unit
async def test_put_rejects_target_that_is_not_an_active_member() -> None:
    from sourcemind.api.v1.github_author_links import GitHubAuthorLinkUpsert, put_github_author_link

    db = _db(member=False)
    with (
        patch(f"{ROUTER}.require_workspace_permission", new=AsyncMock(return_value="admin")),
        pytest.raises(ValidationError),
    ):
        await put_github_author_link(
            workspace_id=uuid.uuid4(),
            github_user_id=101,
            body=GitHubAuthorLinkUpsert(user_id=uuid.uuid4()),
            db=db,
            current_user=_admin(),
        )

    assert not [s for s in db.statements if s[0].startswith("INSERT")]
    db.commit.assert_not_awaited()
    member_sql = next(s[0] for s in db.statements if "workspace_members" in s[0])
    assert "status = 'active'" in member_sql and "departed_at IS NULL" in member_sql


@pytest.mark.unit
@pytest.mark.parametrize(
    "denial", [WorkspaceAccessDeniedError("member"), WorkspaceNotFoundError("x")]
)
async def test_non_admin_cannot_read_or_change_links(denial) -> None:
    from sourcemind.api.v1 import github_author_links as api

    db = _db()
    with patch(f"{ROUTER}.require_workspace_permission", new=AsyncMock(side_effect=denial)):
        with pytest.raises(type(denial)):
            await api.list_github_author_links(
                workspace_id=uuid.uuid4(), db=db, current_user=_admin()
            )
        with pytest.raises(type(denial)):
            await api.put_github_author_link(
                workspace_id=uuid.uuid4(),
                github_user_id=101,
                body=api.GitHubAuthorLinkUpsert(user_id=uuid.uuid4()),
                db=db,
                current_user=_admin(),
            )
        with pytest.raises(type(denial)):
            await api.delete_github_author_link(
                workspace_id=uuid.uuid4(), github_user_id=101, db=db, current_user=_admin()
            )
        with pytest.raises(type(denial)):
            await api.list_github_authors(
                workspace_id=uuid.uuid4(), db=db, current_user=_admin()
            )

    assert db.statements == []


@pytest.mark.unit
async def test_delete_is_scoped_to_workspace_and_numeric_id() -> None:
    from sourcemind.api.v1.github_author_links import delete_github_author_link

    ws = uuid.uuid4()
    db = _db(deleted=True)
    with patch(f"{ROUTER}.require_workspace_permission", new=AsyncMock(return_value="admin")):
        result = await delete_github_author_link(
            workspace_id=ws, github_user_id=101, db=db, current_user=_admin()
        )

    sql, params = next(s for s in db.statements if s[0].startswith("DELETE"))
    assert params == {"ws": str(ws), "gid": 101}
    assert "workspace_id = CAST(:ws AS uuid)" in sql
    assert result == {"deleted": True}
    db.commit.assert_awaited_once()


@pytest.mark.unit
def test_upsert_body_never_accepts_identity_matching_fields() -> None:
    from pydantic import ValidationError as PydanticValidationError

    from sourcemind.api.v1.github_author_links import GitHubAuthorLinkUpsert

    with pytest.raises(PydanticValidationError):
        GitHubAuthorLinkUpsert(user_id=uuid.uuid4(), email="alice@example.com")


@pytest.mark.unit
def test_routes_are_registered_under_v1() -> None:
    # Inspect the ASSEMBLED app's OpenAPI schema, not `api_router.routes`.
    # Newer FastAPI (0.142.x, what an unpinned CI install resolves) includes a
    # router lazily: `api_router.routes` then holds `_IncludedRouter` objects
    # with no `.path`, so a path scan over it sees only {''} although every
    # route is served. Older releases (0.135.x, our lock file) flatten the
    # routes. The OpenAPI schema is the same on both and also pins the methods.
    from sourcemind.main import create_app

    paths = create_app().openapi()["paths"]
    http_methods = {"get", "put", "post", "patch", "delete"}

    def methods(path: str) -> set[str]:
        return {method for method in paths.get(path, {}) if method in http_methods}

    base = "/v1/workspaces/{workspace_id}"
    assert methods(f"{base}/github-author-links") == {"get"}
    assert methods(f"{base}/github-author-links/{{github_user_id}}") == {"put", "delete"}
    assert methods(f"{base}/github-authors") == {"get"}
