"""Unit coverage for POST /v1/workspaces/{id}/members (D-024).

The DB session is a mock whose execute() results are queued in the exact order
the handler issues them:

    1. caller membership lock (FOR SHARE)      -> .first()
    2. target candidates by lower(email)       -> .scalars().all()
    3. existing membership (FOR UPDATE)        -> .first()
    4. (departed) UPDATE ... RETURNING / (none) INSERT via add()+flush()
    5. confirming re-read                      -> .first()

require_workspace_permission is patched except in the tests that exercise its
own 403/404 behaviour. Real-Postgres behaviour (RLS, locks, races) is covered in
tests/integration/test_security_foundation_real_db.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.sql.dml import Update

from sourcemind.core.config import get_settings
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.core.exceptions import (
    RateLimitExceededError,
    ServiceUnavailableError,
    WorkspaceAccessDeniedError,
)

WS_ID = uuid.UUID("00000000-0000-4000-8000-000000000010")
CALLER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
_NOW = datetime(2026, 4, 12, tzinfo=UTC)
_KEY = "550e8400-e29b-41d4-a716-446655440000"
_URL = f"/v1/workspaces/{WS_ID}/members"
_CLERK = "sourcemind.api.v1.workspaces.fetch_clerk_primary_email_verification"
_PERM = "sourcemind.api.v1.workspaces.require_workspace_permission"
_DUP = 'duplicate key value violates unique constraint "uq_workspace_members_workspace_user"'


def _result(*, first=None, users=None) -> MagicMock:
    result = MagicMock()
    result.first.return_value = first
    if users is not None:
        result.scalars.return_value.all.return_value = users
    return result


def _caller_row(role: str = "admin", status: str = "active") -> MagicMock:
    return _result(first=SimpleNamespace(role=role, status=status))


def _user(email: str = "friend@example.com", user_id: uuid.UUID = TARGET_ID):
    return SimpleNamespace(
        id=user_id,
        clerk_id="user_friend",
        email=email,
        display_name="Friend",
        avatar_url=None,
        deleted_at=None,
    )


def _stored(role: str = "member", status: str = "active", departed_at=None) -> MagicMock:
    return _result(
        first=SimpleNamespace(role=role, status=status, departed_at=departed_at, created_at=_NOW)
    )


def _existing(role: str, status: str) -> MagicMock:
    return _result(first=SimpleNamespace(id=uuid.uuid4(), role=role, status=status))


def _session(results: list[MagicMock]) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=list(results))
    session.add = MagicMock()
    session.begin_nested = AsyncMock(return_value=AsyncMock())
    return session


def _app(session: AsyncMock, caller_id: uuid.UUID = CALLER_ID):
    with (
        patch("sourcemind.main.init_db", new_callable=AsyncMock),
        patch("sourcemind.main.init_redis", new_callable=AsyncMock),
        patch("sourcemind.main._init_neo4j", new_callable=AsyncMock),
        patch("sourcemind.main._init_kafka", new_callable=AsyncMock),
        patch("sourcemind.main._close_neo4j", new_callable=AsyncMock),
        patch("sourcemind.main._close_kafka", new_callable=AsyncMock),
        patch("sourcemind.main.close_redis", new_callable=AsyncMock),
        patch("sourcemind.main.close_db", new_callable=AsyncMock),
    ):
        from sourcemind.core.database import get_db_session
        from sourcemind.core.dependencies import get_current_user
        from sourcemind.main import create_app

        app = create_app()

        async def _db():
            yield session

        async def _user_override():
            return AuthenticatedUser(
                user_id=caller_id,
                clerk_id="user_caller",
                email="caller@example.com",
                display_name="Caller",
            )

        app.dependency_overrides[get_db_session] = _db
        app.dependency_overrides[get_current_user] = _user_override
        return app


@pytest.fixture(autouse=True)
def _patches():
    get_settings.cache_clear()
    with patch(
        "sourcemind.api.v1.workspaces.enforce_rate_limit", new_callable=AsyncMock
    ) as limiter:
        yield limiter
    get_settings.cache_clear()


@pytest.fixture
def clerk_enforced():
    """Run with Clerk verification ON (unit env defaults to the dev bypass)."""
    settings = SimpleNamespace(auth_dev_bypass_enabled=False, clerk_secret_key="sk_test_x")
    with patch("sourcemind.api.v1.workspaces.get_settings", return_value=settings):
        yield


def _post(session, body, *, headers=None, perm="admin"):
    app = _app(session)
    hdrs = {"Idempotency-Key": _KEY} if headers is None else headers
    with TestClient(app, raise_server_exceptions=False) as client:
        if perm is None:
            return client.post(_URL, json=body, headers=hdrs)
        with patch(_PERM, new=AsyncMock(return_value=perm)):
            return client.post(_URL, json=body, headers=hdrs)


def _err(resp):
    return resp.json()["error"]


def _update_params(session: AsyncMock) -> dict:
    for call in session.execute.call_args_list:
        stmt = call.args[0]
        if isinstance(stmt, Update):
            return stmt.compile().params
    raise AssertionError("no UPDATE statement was executed")


BODY = {"email": "friend@example.com"}


# ── Authorization ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("role", ["viewer", "member"])
def test_non_admin_caller_gets_403_and_nothing_is_written(role):
    # Real require_workspace_permission: the membership row says viewer/member.
    # (the second queued result is the RLS workspace-context statement)
    session = _session([_result(first=SimpleNamespace(role=role)), MagicMock()])
    resp = _post(session, BODY, perm=None)
    assert resp.status_code == 403
    assert _err(resp)["code"] == "SM005"
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_non_member_and_cross_workspace_admin_get_404_sm022():
    # require_workspace_member finds no ACTIVE row in THIS workspace; an admin of
    # another workspace is indistinguishable from a stranger.
    session = _session([_result(first=None)])
    resp = _post(session, BODY, perm=None)
    assert resp.status_code == 404
    assert _err(resp)["code"] == "SM022"
    session.commit.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lock_row", "status_code", "code"),
    [
        (None, 404, "SM022"),  # caller revoked: row invisible after the lock
        (SimpleNamespace(role="admin", status="departed"), 404, "SM022"),
        (SimpleNamespace(role="member", status="active"), 403, "SM005"),
    ],
)
def test_caller_is_reverified_after_taking_the_share_lock(lock_row, status_code, code):
    session = _session([_result(users=[_user()]), _result(first=lock_row)])
    resp = _post(session, BODY)
    assert resp.status_code == status_code
    assert _err(resp)["code"] == code
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_rate_limit_is_checked_first_and_returns_429(_patches):
    _patches.side_effect = RateLimitExceededError("slow down", retry_after_seconds=7)
    session = _session([])
    resp = _post(session, BODY)
    assert resp.status_code == 429
    assert _err(resp)["code"] == "SM015"
    assert resp.headers["Retry-After"] == "7"
    session.execute.assert_not_awaited()
    assert _patches.await_args.args[0].value == "member-add"
    assert _patches.await_args.args[1] == CALLER_ID


# ── Request validation ────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "body",
    [
        {"email": "friend@example.com", "user_id": str(TARGET_ID)},
        {"email": "friend@example.com", "email_verified": True},
        {"email": "friend@example.com", "role": "owner"},
        {"email": "friend@example.com", "role": "admin"},
        {"email": "not-an-email"},
        {"role": "member"},
    ],
)
def test_extra_fields_privileged_roles_and_bad_email_are_422(body):
    session = _session([])
    resp = _post(session, body)
    assert resp.status_code == 422
    session.execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "not-a-uuid"}])
def test_missing_or_invalid_idempotency_key_is_400_sm013(headers):
    session = _session([])
    resp = _post(session, BODY, headers=headers)
    assert resp.status_code == 400
    assert _err(resp)["code"] == "SM013"
    session.execute.assert_not_awaited()


# ── Target resolution and Clerk verification ──────────────────────────────────


@pytest.mark.unit
def test_unknown_email_is_404_user_not_found_with_sign_in_hint():
    session = _session([_result(users=[])])
    resp = _post(session, BODY)
    assert resp.status_code == 404
    error = _err(resp)
    assert error["code"] == "SM023"
    assert error["details"] == {"reason": "user_not_found"}
    assert "sign in" in error["message"]


@pytest.mark.unit
def test_case_variant_duplicates_are_422_ambiguous_and_never_picked(clerk_enforced):
    session = _session(
        [
            _result(users=[_user("Friend@Example.com"), _user("friend@example.com", uuid.uuid4())]),
            _caller_row(),
        ]
    )
    with patch(_CLERK, new=AsyncMock()) as clerk:
        resp = _post(session, {"email": "FRIEND@example.com"})
    assert resp.status_code == 422
    assert _err(resp)["details"] == {"reason": "email_ambiguous"}
    clerk.assert_not_awaited()
    session.add.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "clerk_result",
    [
        ("friend@example.com", "unverified"),
        ("friend@example.com", None),
        ("", None),
        ("someone.else@example.com", "verified"),
    ],
)
def test_clerk_must_confirm_verified_primary_equal_to_stored_and_requested(
    clerk_enforced, clerk_result
):
    session = _session([_result(users=[_user()])])
    with patch(_CLERK, new=AsyncMock(return_value=clerk_result)):
        resp = _post(session, BODY)
    assert resp.status_code == 422
    assert _err(resp)["details"] == {"reason": "email_not_verified"}
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_stored_email_differing_from_clerk_primary_is_422(clerk_enforced):
    # Stored address predates a Clerk-side change; Clerk now reports another one.
    session = _session([_result(users=[_user("friend@example.com")])])
    with patch(_CLERK, new=AsyncMock(return_value=("new@example.com", "verified"))):
        resp = _post(session, BODY)
    assert resp.status_code == 422
    assert _err(resp)["details"]["reason"] == "email_not_verified"


@pytest.mark.unit
def test_clerk_unreachable_fails_closed_with_503(clerk_enforced):
    session = _session([_result(users=[_user()])])
    unreachable = AsyncMock(side_effect=ServiceUnavailableError("clerk down"))
    with patch(_CLERK, new=unreachable):
        resp = _post(session, BODY)
    assert resp.status_code == 503
    assert _err(resp)["code"] == "SM055"
    session.add.assert_not_called()


@pytest.mark.unit
def test_development_bypass_skips_clerk_entirely():
    session = _session([_result(users=[_user()]), _caller_row(), _result(first=None), _stored()])
    with patch(_CLERK, new=AsyncMock()) as clerk:
        resp = _post(session, BODY)  # unit env: AUTH_DEV_BYPASS_ENABLED=true
    assert resp.status_code == 201
    clerk.assert_not_awaited()


# ── Membership state machine ──────────────────────────────────────────────────


@pytest.mark.unit
def test_success_inserts_requested_role_and_returns_201_shape(clerk_enforced):
    session = _session(
        [
            _result(users=[_user()]),
            _caller_row("owner"),
            _result(first=None),
            _stored("viewer"),
        ]
    )
    with patch(_CLERK, new=AsyncMock(return_value=("Friend@Example.com", "verified"))):
        resp = _post(session, {"email": "Friend@Example.com", "role": "viewer"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["data"] == {
        "user": {"id": str(TARGET_ID), "display_name": "Friend", "avatar_url": None},
        "role": "viewer",
        "joined_at": "2026-04-12T00:00:00Z",
    }
    assert "request_id" in body["meta"]
    added = session.add.call_args.args[0]
    assert (added.workspace_id, added.user_id) == (WS_ID, TARGET_ID)
    assert (added.role, added.status, added.invited_by_id) == ("viewer", "active", CALLER_ID)
    session.commit.assert_awaited_once()


@pytest.mark.unit
def test_role_defaults_to_member():
    session = _session(
        [_result(users=[_user()]), _caller_row(), _result(first=None), _stored("member")]
    )
    resp = _post(session, BODY)
    assert resp.status_code == 201
    assert session.add.call_args.args[0].role == "member"


@pytest.mark.unit
def test_duplicate_active_member_is_409_already_member():
    session = _session([_result(users=[_user()]), _caller_row(), _existing("member", "active")])
    resp = _post(session, BODY)
    assert resp.status_code == 409
    assert _err(resp)["code"] == "SM036"
    assert _err(resp)["details"] == {"reason": "already_member"}
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_departing_member_is_409_handoff_in_progress():
    session = _session([_result(users=[_user()]), _caller_row(), _existing("member", "departing")])
    resp = _post(session, BODY)
    assert resp.status_code == 409
    assert _err(resp)["code"] == "SM036"
    assert _err(resp)["details"] == {"reason": "handoff_in_progress"}
    assert not any(isinstance(c.args[0], Update) for c in session.execute.call_args_list)


@pytest.mark.unit
@pytest.mark.parametrize("old_role", ["admin", "member", "viewer"])
def test_reactivation_writes_requested_role_and_never_restores_old_role(old_role):
    session = _session(
        [
            _result(users=[_user()]),
            _caller_row("owner"),
            _existing(old_role, "departed"),
            _result(first=SimpleNamespace(id=uuid.uuid4())),
            _stored("member"),
        ]
    )
    resp = _post(session, BODY)
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["role"] == "member"
    params = _update_params(session)
    assert params["role"] == "member"
    assert params["status"] == "active"
    assert params["departed_at"] is None
    assert params["invited_by_id"] == CALLER_ID
    assert "admin" not in params.values() and "owner" not in params.values()
    session.add.assert_not_called()
    session.commit.assert_awaited_once()


@pytest.mark.unit
def test_non_owner_cannot_reactivate_a_former_owner_row_even_silently():
    # RLS USING filters the owner row: UPDATE ... RETURNING yields no row.
    session = _session(
        [
            _result(users=[_user()]),
            _caller_row("admin"),
            _existing("owner", "departed"),
            _result(first=None),
        ]
    )
    resp = _post(session, BODY)
    assert resp.status_code == 403
    assert _err(resp)["code"] == "SM005"
    assert "former owner" in _err(resp)["message"]
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_owner_may_readd_a_former_owner_as_an_ordinary_member():
    session = _session(
        [
            _result(users=[_user()]),
            _caller_row("owner"),
            _existing("owner", "departed"),
            _result(first=SimpleNamespace(id=uuid.uuid4())),
            _stored("member"),
        ]
    )
    resp = _post(session, BODY)
    assert resp.status_code == 201
    assert _update_params(session)["role"] == "member"


@pytest.mark.unit
def test_concurrent_insert_loser_maps_integrity_error_to_409():
    session = _session(
        [
            _result(users=[_user()]),
            _caller_row(),
            _result(first=None),
            _result(first=SimpleNamespace(role="member", status="active")),  # winner
        ]
    )
    session.flush = AsyncMock(
        side_effect=IntegrityError(
            "INSERT",
            {},
            Exception(_DUP),
        )
    )
    resp = _post(session, BODY)
    assert resp.status_code == 409
    assert _err(resp)["details"] == {"reason": "already_member"}
    session.begin_nested.return_value.rollback.assert_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_hidden_former_owner_row_surfaces_as_403_not_already_member():
    # FOR UPDATE skipped the row (RLS), the INSERT then hit the unique index.
    session = _session(
        [
            _result(users=[_user()]),
            _caller_row("admin"),
            _result(first=None),
            _result(first=SimpleNamespace(role="owner", status="departed")),
        ]
    )
    session.flush = AsyncMock(
        side_effect=IntegrityError(
            "INSERT",
            {},
            Exception(_DUP),
        )
    )
    resp = _post(session, BODY)
    assert resp.status_code == 403
    assert "former owner" in _err(resp)["message"]


@pytest.mark.unit
def test_rls_denial_during_insert_is_403_not_500():
    class _Orig(Exception):
        sqlstate = "42501"

    session = _session([_result(users=[_user()]), _caller_row(), _result(first=None)])
    session.flush = AsyncMock(
        side_effect=DBAPIError("INSERT", {}, _Orig("new row violates row-level security policy"))
    )
    resp = _post(session, BODY)
    assert resp.status_code == 403
    assert _err(resp)["code"] == "SM005"
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_rls_denial_during_reactivation_is_403_not_500():
    class _Orig(Exception):
        sqlstate = "42501"

    session = _session([])
    queued = iter([_result(users=[_user()]), _caller_row(), _existing("member", "departed")])

    async def _execute(stmt, *_a, **_kw):
        if isinstance(stmt, Update):
            raise DBAPIError("UPDATE", {}, _Orig("row-level security"))
        return next(queued)

    session.execute = AsyncMock(side_effect=_execute)
    resp = _post(session, BODY)
    assert resp.status_code == 403
    assert _err(resp)["code"] == "SM005"


@pytest.mark.unit
def test_unconfirmed_write_is_not_reported_as_success():
    # Re-read shows a different role than requested (policy/trigger interfered).
    session = _session(
        [_result(users=[_user()]), _caller_row(), _result(first=None), _stored("admin")]
    )
    resp = _post(session, BODY)
    assert resp.status_code == 503
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_workspace_access_denied_error_is_the_documented_class():
    # Guards the mapping target used for RLS denials.
    assert WorkspaceAccessDeniedError.http_status == 403


# ── Review round: locking order, self-add, retryable aborts, scoping ─────────


@pytest.mark.unit
def test_workspace_wide_rate_limit_is_checked_in_addition_to_the_user_limit(_patches):
    session = _session([_result(users=[])])
    _post(session, BODY)
    ops = [call.args[0].value for call in _patches.await_args_list]
    assert ops == ["member-add", "member-add-workspace"]
    # Workspace counter: keyed by the workspace id (subject and scope).
    assert _patches.await_args_list[1].args[1:] == (WS_ID, WS_ID)


@pytest.mark.unit
def test_adding_yourself_is_refused_without_locks_or_clerk(clerk_enforced):
    me = _user("caller@example.com", CALLER_ID)
    session = _session([_result(users=[me])])
    with patch(_CLERK, new=AsyncMock()) as clerk:
        resp = _post(session, {"email": "caller@example.com"})
    assert resp.status_code == 409
    assert _err(resp)["details"] == {"reason": "already_member"}
    clerk.assert_not_awaited()
    assert session.execute.await_count == 1  # candidate lookup only: no row locks
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_membership_rows_are_locked_in_ascending_user_id_order():
    # Caller id sorts after the target id here, so the TARGET row is locked first.
    low, high = sorted([uuid.uuid4(), uuid.uuid4()], key=str)
    seen: list[str] = []

    async def _execute(stmt, *_a, **_kw):
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        if "FOR SHARE" in sql:
            seen.append("caller")
            return _caller_row()
        if "FOR UPDATE" in sql:
            seen.append("target")
            return _existing("member", "active")
        return _result(users=[_user(user_id=low)])

    session = _session([])
    session.execute = AsyncMock(side_effect=_execute)
    app = _app(session, caller_id=high)
    with (
        TestClient(app, raise_server_exceptions=False) as client,
        patch(_PERM, new=AsyncMock(return_value="admin")),
    ):
        resp = client.post(_URL, json=BODY, headers={"Idempotency-Key": _KEY})
    assert resp.status_code == 409
    assert seen == ["target", "caller"]


@pytest.mark.unit
@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
def test_deadlock_or_serialization_abort_is_a_retryable_503_not_500(sqlstate):
    class _Orig(Exception):
        pass

    _Orig.sqlstate = sqlstate  # type: ignore[attr-defined]
    session = _session([_result(users=[_user()]), _caller_row(), _result(first=None)])
    session.flush = AsyncMock(side_effect=DBAPIError("INSERT", {}, _Orig("deadlock detected")))
    resp = _post(session, BODY)
    assert resp.status_code == 503
    assert _err(resp)["code"] == "SM055"
    assert _err(resp)["details"] == {"reason": "concurrent_update_retry"}
    session.rollback.assert_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.unit
def test_unrelated_integrity_error_is_not_reported_as_already_member():
    session = _session([_result(users=[_user()]), _caller_row(), _result(first=None)])
    session.flush = AsyncMock(
        side_effect=IntegrityError(
            "INSERT",
            {},
            Exception('violates foreign key constraint "workspace_members_user_id_fkey"'),
        )
    )
    resp = _post(session, BODY)
    assert resp.status_code == 500
    assert _err(resp)["code"] != "SM036"


@pytest.mark.unit
@pytest.mark.parametrize("email", ["josé@example.com", "friend@examplé.com"])
def test_non_ascii_emails_are_rejected_before_any_lookup(email):
    session = _session([])
    resp = _post(session, {"email": email})
    assert resp.status_code == 422
    session.execute.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize("role", ["viewer", "member"])
def test_non_admins_cannot_spend_the_shared_workspace_bucket(_patches, role):
    # Real permission check: the caller is a plain viewer/member of the workspace.
    session = _session([_result(first=SimpleNamespace(role=role)), MagicMock()])
    for _ in range(3):
        resp = _post(session, BODY, perm=None)
        assert resp.status_code in (403, 404)
        session.execute.side_effect = [_result(first=SimpleNamespace(role=role)), MagicMock()]
    ops = [call.args[0].value for call in _patches.await_args_list]
    # Only the per-user limiter ran; the workspace-wide bucket was never touched.
    assert ops == ["member-add"] * 3


@pytest.mark.unit
def test_an_admin_still_spends_both_limiters_and_can_add(_patches):
    session = _session([_result(users=[_user()]), _caller_row(), _result(first=None), _stored()])
    resp = _post(session, BODY)
    assert resp.status_code == 201
    ops = [call.args[0].value for call in _patches.await_args_list]
    assert ops == ["member-add", "member-add-workspace"]
