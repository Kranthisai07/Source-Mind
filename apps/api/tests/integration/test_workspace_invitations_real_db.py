# ruff: noqa: F811  (pytest fixtures are used by parameter name)
"""Workspace invitations (D-065) against a real PostgreSQL on the restricted runtime role.

Handlers are called directly with real sessions, so row-level security, the policy predicates,
row locks and triggers are real. Only the Redis rate limiter and the Clerk lookup are replaced.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.api.v1.invitations import (
    accept_workspace_invitation,
    create_workspace_invitation,
    list_workspace_invitations,
    revoke_workspace_invitation,
)
from sourcemind.api.v1.workspaces import change_workspace_member_role, revoke_workspace_member
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.exceptions import (
    InvitationNotFoundError,
    ValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceMemberExistsError,
    WorkspaceNotFoundError,
)
from sourcemind.models.workspace import WorkspaceMember
from sourcemind.models.workspace_invitation import WorkspaceInvitation
from sourcemind.schemas.invitation import InvitationAccept, InvitationCreate
from sourcemind.schemas.workspace import WorkspaceMemberRoleUpdate
from sourcemind.services.workspace.invitations import FOUNDER_ONLY, generate_token, hash_token
from tests.integration.test_graph_outbox_real_db import owner_engine  # noqa: F401  (fixture)
from tests.integration.test_security_foundation_real_db import (
    _actor,
    _email,
    _seed_security_tenants,
    owner_race_database_url,  # noqa: F401  (fixture used by owner_race_engine)
    owner_race_engine,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def clerk():
    """Replace Redis and Clerk. The returned dict overrides a clerk_id's (email, verification)."""
    overrides: dict[str, tuple[str, str]] = {}

    async def lookup(clerk_id: str, _secret: str) -> tuple[str, str]:
        return overrides.get(clerk_id, (f"{clerk_id}@example.com", "verified"))

    settings = SimpleNamespace(auth_dev_bypass_enabled=False, clerk_secret_key="sk_test_x")
    with (
        patch("sourcemind.api.v1.invitations.enforce_rate_limit", new=AsyncMock()),
        patch("sourcemind.api.v1.workspaces.enforce_rate_limit", new=AsyncMock()),
        patch("sourcemind.api.v1.invitations.get_settings", return_value=settings),
        patch(
            "sourcemind.api.v1.invitations.fetch_clerk_primary_email_verification", new=lookup
        ),
    ):
        yield overrides


def _fresh_email() -> str:
    return f"newcomer-{uuid.uuid4().hex[:10]}@example.com"


async def _invite(engine, data, actor_id, email, role="member"):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, actor_id)
        try:
            return await create_workspace_invitation(
                workspace_id=data.target_workspace_id,
                body=InvitationCreate(email=email, role=role),
                db=session,
                current_user=_actor(data, actor_id),
                request_id="test",
                idempotency_key=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            return exc


async def _accept(engine, data, user_id, token):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, user_id)
        try:
            return await accept_workspace_invitation(
                body=InvitationAccept(token=token),
                db=session,
                current_user=_actor(data, user_id),
                request_id="test",
                idempotency_key=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            return exc


async def _revoke_invite(engine, data, actor_id, invitation_id):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, actor_id)
        try:
            return await revoke_workspace_invitation(
                workspace_id=data.target_workspace_id,
                invitation_id=invitation_id,
                db=session,
                current_user=_actor(data, actor_id),
                idempotency_key=str(uuid.uuid4()),
            )
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            return exc


async def _list(engine, data, actor_id):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, actor_id)
        return await list_workspace_invitations(
            workspace_id=data.target_workspace_id,
            db=session,
            current_user=_actor(data, actor_id),
            request_id="test",
        )


async def _rows(engine, data, email) -> list[WorkspaceInvitation]:
    """Every invitation (any state) for this address, read as the first founder."""
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        rows = (
            await session.execute(
                select(WorkspaceInvitation).where(
                    WorkspaceInvitation.workspace_id == data.target_workspace_id,
                    WorkspaceInvitation.email == email,
                )
            )
        ).scalars().all()
        session.expunge_all()
        return list(rows)


async def _membership(engine, data, user_id) -> tuple[str, str] | None:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        row = (
            await session.execute(
                select(WorkspaceMember.role, WorkspaceMember.status).where(
                    WorkspaceMember.workspace_id == data.target_workspace_id,
                    WorkspaceMember.user_id == user_id,
                )
            )
        ).first()
        return None if row is None else (str(row.role), str(row.status))


async def _owner_count(engine, data) -> int:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        return int(
            await session.scalar(
                select(func.count())
                .select_from(WorkspaceMember)
                .where(
                    WorkspaceMember.workspace_id == data.target_workspace_id,
                    WorkspaceMember.role == "owner",
                    WorkspaceMember.status == "active",
                    WorkspaceMember.departed_at.is_(None),
                )
            )
        )


def _ok(outcome):
    assert not isinstance(outcome, Exception), outcome
    return outcome


# ── creating ─────────────────────────────────────────────────────────────────────────────


async def test_the_token_is_shown_once_and_only_its_hash_is_stored(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    email = _fresh_email()
    created = _ok(await _invite(supabase_engine, data, data.owner_id, email)).data
    assert created.token.startswith("smi_") and created.accept_path == f"/invite/{created.token}"

    (row,) = await _rows(supabase_engine, data, email)
    assert row.token_hash == hashlib.sha256(created.token.encode()).hexdigest()
    assert created.token not in {str(v) for v in vars(row).values()}

    listed = _ok(await _list(supabase_engine, data, data.owner_id)).data
    mine = [item for item in listed if item.email == email]
    assert len(mine) == 1 and "token" not in mine[0].model_dump()


async def test_a_founder_invites_as_any_role_and_an_admin_only_as_teammate_or_viewer(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    for role in ("owner", "admin", "member", "viewer"):
        _ok(await _invite(supabase_engine, data, data.owner_id, _fresh_email(), role))
    for role in ("member", "viewer"):
        _ok(await _invite(supabase_engine, data, data.admin_id, _fresh_email(), role))
    for role in ("owner", "admin"):
        denied = await _invite(supabase_engine, data, data.admin_id, _fresh_email(), role)
        assert isinstance(denied, WorkspaceAccessDeniedError), (role, denied)
        assert FOUNDER_ONLY in str(denied)


async def test_teammates_viewers_and_outsiders_cannot_invite(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    for actor in (data.member_id, data.viewer_id):
        denied = await _invite(supabase_engine, data, actor, _fresh_email())
        assert isinstance(denied, WorkspaceAccessDeniedError), (actor, denied)
    for actor in (data.zero_membership_id, data.other_workspace_member_id):
        gone = await _invite(supabase_engine, data, actor, _fresh_email())
        assert isinstance(gone, WorkspaceNotFoundError), (actor, gone)


async def test_inviting_an_existing_member_is_refused(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    outcome = await _invite(supabase_engine, data, data.owner_id, _email(data, data.member_id))
    assert isinstance(outcome, WorkspaceMemberExistsError)


async def test_a_new_invitation_replaces_the_old_one(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    email = _email(data, data.zero_membership_id)
    first = _ok(await _invite(supabase_engine, data, data.owner_id, email, "viewer")).data
    second = _ok(await _invite(supabase_engine, data, data.owner_id, email, "member")).data
    assert first.token != second.token

    stale = await _accept(supabase_engine, data, data.zero_membership_id, first.token)
    assert isinstance(stale, InvitationNotFoundError)
    listed = _ok(await _list(supabase_engine, data, data.owner_id)).data
    pending = [i for i in listed if i.email == email]
    assert [i.role for i in pending] == ["member"]

    joined = _ok(await _accept(supabase_engine, data, data.zero_membership_id, second.token)).data
    assert joined.role == "member"


async def test_an_admin_cannot_replace_a_founders_pending_invitation(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    email = _fresh_email()
    _ok(await _invite(supabase_engine, data, data.owner_id, email, "admin"))
    outcome = await _invite(supabase_engine, data, data.admin_id, email, "member")
    assert isinstance(outcome, ValidationError)
    assert outcome.details == {"reason": "pending_exists"}
    live = [r for r in await _rows(supabase_engine, data, email) if r.revoked_at is None]
    assert [r.role for r in live] == ["admin"]


# ── accepting ────────────────────────────────────────────────────────────────────────────


async def test_accepting_creates_a_membership_with_the_invited_role_once(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    email = _email(data, data.zero_membership_id)
    token = _ok(await _invite(supabase_engine, data, data.owner_id, email, "viewer")).data.token

    joined = _ok(await _accept(supabase_engine, data, data.zero_membership_id, token)).data
    assert joined.workspace_id == data.target_workspace_id and joined.role == "viewer"
    assert await _membership(supabase_engine, data, data.zero_membership_id) == ("viewer", "active")
    (row,) = await _rows(supabase_engine, data, email)
    assert row.accepted_at is not None and row.accepted_by == data.zero_membership_id
    listed = _ok(await _list(supabase_engine, data, data.owner_id)).data
    assert not [i for i in listed if i.email == email]

    reused = await _accept(supabase_engine, data, data.zero_membership_id, token)
    assert isinstance(reused, InvitationNotFoundError)


async def test_accepting_a_founder_invitation_makes_a_co_founder(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    email = _email(data, data.zero_membership_id)
    token = _ok(await _invite(supabase_engine, data, data.owner_id, email, "owner")).data.token
    assert await _owner_count(supabase_engine, data) == 2
    _ok(await _accept(supabase_engine, data, data.zero_membership_id, token))
    assert await _membership(supabase_engine, data, data.zero_membership_id) == ("owner", "active")
    assert await _owner_count(supabase_engine, data) == 3


async def test_the_wrong_person_cannot_accept_and_the_invitation_survives(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    email = _email(data, data.zero_membership_id)
    token = _ok(await _invite(supabase_engine, data, data.owner_id, email)).data.token

    stolen = await _accept(supabase_engine, data, data.other_workspace_member_id, token)
    assert isinstance(stolen, InvitationNotFoundError)
    assert await _membership(supabase_engine, data, data.other_workspace_member_id) is None
    (row,) = await _rows(supabase_engine, data, email)
    assert row.accepted_at is None and row.revoked_at is None

    _ok(await _accept(supabase_engine, data, data.zero_membership_id, token))


async def test_an_unverified_or_changed_email_cannot_accept(supabase_engine, clerk) -> None:
    data = await _seed_security_tenants(supabase_engine)
    email = _email(data, data.zero_membership_id)
    token = _ok(await _invite(supabase_engine, data, data.owner_id, email)).data.token
    clerk_id = data.clerk_ids[data.zero_membership_id]

    clerk[clerk_id] = (email, "unverified")
    zero = data.zero_membership_id
    assert isinstance(await _accept(supabase_engine, data, zero, token), InvitationNotFoundError)
    clerk[clerk_id] = ("someone-else@example.com", "verified")
    assert isinstance(await _accept(supabase_engine, data, zero, token), InvitationNotFoundError)
    assert await _membership(supabase_engine, data, data.zero_membership_id) is None


async def test_expired_unknown_and_revoked_invitations_all_look_the_same(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id
    email = _email(data, zero)

    # Expired: written directly, as the founder, with dates in the past.
    stale_token, _ = generate_token()  # random: the test database persists between runs
    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        past = datetime.now(UTC) - timedelta(days=8)
        session.add(
            WorkspaceInvitation(
                workspace_id=data.target_workspace_id,
                email=email,
                role="member",
                token_hash=hash_token(stale_token),
                invited_by=data.owner_id,
                created_at=past,
                expires_at=past + timedelta(days=7),
            )
        )
        await session.commit()
    expired = await _accept(supabase_engine, data, zero, stale_token)
    unknown = await _accept(supabase_engine, data, zero, "smi_" + "B" * 43)

    # Revoked (this also supersedes the expired row above, which is fine: it was already dead).
    live = _ok(await _invite(supabase_engine, data, data.owner_id, email, "member")).data
    wanted = hash_token(live.token)
    (invitation,) = [r for r in await _rows(supabase_engine, data, email) if r.token_hash == wanted]
    assert await _revoke_invite(supabase_engine, data, data.admin_id, invitation.id) is None
    revoked = await _accept(supabase_engine, data, zero, live.token)

    for outcome in (expired, unknown, revoked):
        assert isinstance(outcome, InvitationNotFoundError)
    assert len({str(o) for o in (expired, unknown, revoked)}) == 1, "one answer, no oracle"
    assert await _membership(supabase_engine, data, zero) is None


async def test_admins_revoke_teammate_invitations_but_only_founders_revoke_admin_ones(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    admin_email, member_email = _fresh_email(), _fresh_email()
    _ok(await _invite(supabase_engine, data, data.owner_id, admin_email, "admin"))
    _ok(await _invite(supabase_engine, data, data.owner_id, member_email, "member"))
    (admin_inv,) = await _rows(supabase_engine, data, admin_email)
    (member_inv,) = await _rows(supabase_engine, data, member_email)

    assert isinstance(
        await _revoke_invite(supabase_engine, data, data.admin_id, admin_inv.id),
        WorkspaceAccessDeniedError,
    )
    assert await _revoke_invite(supabase_engine, data, data.admin_id, member_inv.id) is None
    assert await _revoke_invite(supabase_engine, data, data.owner_id, admin_inv.id) is None
    assert isinstance(
        await _revoke_invite(supabase_engine, data, data.owner_id, uuid.uuid4()),
        InvitationNotFoundError,
    )
    for actor in (data.member_id, data.viewer_id):
        assert isinstance(
            await _revoke_invite(supabase_engine, data, actor, admin_inv.id),
            WorkspaceAccessDeniedError,
        )


# ── authority is rechecked when the invitation is used ───────────────────────────────────


async def test_an_invitation_dies_when_its_inviter_loses_the_authority_it_needed(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id

    # A founder invites someone as admin, then is demoted: the invitation must stop working.
    token = _ok(
        await _invite(supabase_engine, data, data.backup_owner_id, _email(data, zero), "admin")
    ).data.token
    async with AsyncSession(supabase_engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, data.owner_id)
        await change_workspace_member_role(
            workspace_id=data.target_workspace_id,
            user_id=data.backup_owner_id,
            body=WorkspaceMemberRoleUpdate(role="member"),
            db=session,
            current_user=_actor(data, data.owner_id),
            request_id="test",
            idempotency_key=str(uuid.uuid4()),
        )
    outcome = await _accept(supabase_engine, data, zero, token)
    assert isinstance(outcome, InvitationNotFoundError)
    assert await _membership(supabase_engine, data, zero) is None


async def test_an_admins_teammate_invitation_dies_if_the_admin_is_demoted(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id
    token = _ok(
        await _invite(supabase_engine, data, data.admin_id, _email(data, zero), "member")
    ).data.token
    async with AsyncSession(supabase_engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, data.owner_id)
        await change_workspace_member_role(
            workspace_id=data.target_workspace_id,
            user_id=data.admin_id,
            body=WorkspaceMemberRoleUpdate(role="viewer"),
            db=session,
            current_user=_actor(data, data.owner_id),
            request_id="test",
            idempotency_key=str(uuid.uuid4()),
        )
    assert isinstance(await _accept(supabase_engine, data, zero, token), InvitationNotFoundError)
    assert await _membership(supabase_engine, data, zero) is None


# ── people who already have a row ────────────────────────────────────────────────────────


async def test_a_returning_member_can_be_invited_back_in_a_new_role(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    returning = data.member_id
    async with AsyncSession(supabase_engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, data.owner_id)
        await revoke_workspace_member(
            workspace_id=data.target_workspace_id,
            user_id=returning,
            db=session,
            current_user=_actor(data, data.owner_id),
            idempotency_key=str(uuid.uuid4()),
        )
    assert await _membership(supabase_engine, data, returning) == ("member", "departed")

    token = _ok(
        await _invite(supabase_engine, data, data.owner_id, _email(data, returning), "viewer")
    ).data.token
    _ok(await _accept(supabase_engine, data, returning, token))
    assert await _membership(supabase_engine, data, returning) == ("viewer", "active")


async def test_an_invitation_never_reactivates_a_member_mid_handoff(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    member = data.member_id
    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        await session.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == member,
            )
            .values(status="departing")
        )
        await session.commit()
    token = _ok(
        await _invite(supabase_engine, data, data.owner_id, _email(data, member), "admin")
    ).data.token
    assert isinstance(await _accept(supabase_engine, data, member, token), InvitationNotFoundError)
    assert await _membership(supabase_engine, data, member) == ("member", "departing")


async def test_an_active_member_cannot_use_an_invitation_to_change_their_role(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id
    token = _ok(
        await _invite(supabase_engine, data, data.owner_id, _email(data, zero), "admin")
    ).data.token
    async with AsyncSession(supabase_engine) as session:  # joined some other way meanwhile
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        session.add(
            WorkspaceMember(workspace_id=data.target_workspace_id, user_id=zero, role="viewer")
        )
        await session.commit()
    assert isinstance(await _accept(supabase_engine, data, zero, token), InvitationNotFoundError)
    assert await _membership(supabase_engine, data, zero) == ("viewer", "active")


async def test_two_simultaneous_accepts_make_exactly_one_membership(owner_race_engine) -> None:
    data = await _seed_security_tenants(owner_race_engine)
    zero = data.zero_membership_id
    token = _ok(
        await _invite(owner_race_engine, data, data.owner_id, _email(data, zero), "member")
    ).data.token
    outcomes = await asyncio.gather(
        _accept(owner_race_engine, data, zero, token),
        _accept(owner_race_engine, data, zero, token),
    )
    assert sum(not isinstance(o, Exception) for o in outcomes) == 1, outcomes
    assert sum(isinstance(o, InvitationNotFoundError) for o in outcomes) == 1, outcomes
    assert await _membership(owner_race_engine, data, zero) == ("member", "active")


# ── what the database enforces by itself ─────────────────────────────────────────────────


async def test_the_database_refuses_an_admin_inviting_as_admin_even_if_the_app_did_not(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.admin_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        session.add(
            WorkspaceInvitation(
                workspace_id=data.target_workspace_id,
                email=_fresh_email(),
                role="admin",
                token_hash=hash_token("smi_" + "C" * 43),
                invited_by=data.admin_id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            await session.flush()


async def test_the_runtime_role_cannot_delete_or_rewrite_invitations(supabase_engine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    _ok(await _invite(supabase_engine, data, data.owner_id, _fresh_email()))
    for statement in (
        "DELETE FROM workspace_invitations",
        "UPDATE workspace_invitations SET role = 'owner'",
        "UPDATE workspace_invitations SET email = 'x@y.z'",
        "UPDATE workspace_invitations SET token_hash = repeat('0', 64)",
        "UPDATE workspace_invitations SET expires_at = now() + interval '3650 days'",
    ):
        async with AsyncSession(supabase_engine) as session:
            await set_rls_user_context(session, data.owner_id)
            await set_rls_workspace_context(session, data.target_workspace_id)
            with pytest.raises(DBAPIError, match="permission denied"):
                await session.execute(text(statement))


async def test_a_token_unlocks_exactly_one_invitation_and_nobody_else_sees_any(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id
    token = _ok(
        await _invite(supabase_engine, data, data.owner_id, _email(data, zero), "member")
    ).data.token
    _ok(await _invite(supabase_engine, data, data.owner_id, _fresh_email(), "viewer"))

    async def visible(hash_value: str | None) -> int:
        async with AsyncSession(supabase_engine) as session:
            await set_rls_user_context(session, zero)
            if hash_value is not None:
                await session.execute(
                    text("SELECT set_config('app.invitation_token_hash', :h, true)"),
                    {"h": hash_value},
                )
            return int(await session.scalar(text("SELECT count(*) FROM workspace_invitations")))

    assert await visible(None) == 0
    assert await visible(hash_token("smi_" + "D" * 43)) == 0
    assert await visible(hash_token(token)) == 1


async def test_a_member_token_cannot_create_a_founder_row_or_a_row_for_someone_else(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id
    token = _ok(
        await _invite(supabase_engine, data, data.owner_id, _email(data, zero), "member")
    ).data.token

    async def try_insert(user_id: uuid.UUID, role: str, *, hash_value: str, email: str) -> None:
        async with AsyncSession(supabase_engine) as session:
            await set_rls_user_context(session, zero)
            await session.execute(
                text(
                    "SELECT set_config('app.invitation_token_hash', :h, true), "
                    "set_config('app.invitation_email', :e, true)"
                ),
                {"h": hash_value, "e": email},
            )
            session.add(
                WorkspaceMember(workspace_id=data.target_workspace_id, user_id=user_id, role=role)
            )
            await session.flush()
            await session.rollback()

    good = hash_token(token)
    mine = _email(data, zero)
    with pytest.raises(DBAPIError, match="row-level security"):
        # a role the token does not grant
        await try_insert(zero, "owner", hash_value=good, email=mine)
    with pytest.raises(DBAPIError, match="row-level security"):
        # someone else's row
        await try_insert(data.viewer_id, "member", hash_value=good, email=mine)
    with pytest.raises(DBAPIError, match="row-level security"):
        await try_insert(zero, "member", hash_value=good, email="other@example.com")  # wrong email
    with pytest.raises(DBAPIError, match="row-level security"):
        await try_insert(zero, "member", hash_value=hash_token("smi_" + "E" * 43), email=mine)
    await try_insert(zero, "member", hash_value=good, email=mine)  # the one thing it does allow


async def test_deleting_a_user_keeps_their_finished_invitations_with_the_reference_cleared(
    supabase_engine, owner_engine
) -> None:
    """Codex, #48: the finished-invitations trigger must not block ON DELETE SET NULL.

    Run as the table owner, as an erasure job or a migration would. The invitation row stays,
    its accepted_by reference is cleared, and nothing else about it changes.
    """
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id
    email = _email(data, zero)
    token = _ok(await _invite(supabase_engine, data, data.owner_id, email, "viewer")).data.token
    _ok(await _accept(supabase_engine, data, zero, token))
    revoked_email = _fresh_email()
    _ok(await _invite(supabase_engine, data, data.owner_id, revoked_email, "member"))
    (to_revoke,) = await _rows(supabase_engine, data, revoked_email)
    assert await _revoke_invite(supabase_engine, data, data.backup_owner_id, to_revoke.id) is None

    async with owner_engine.begin() as conn:
        await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": zero})
        await conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": data.backup_owner_id})

    (accepted,) = await _rows(supabase_engine, data, email)
    assert accepted.accepted_at is not None and accepted.accepted_by is None
    assert accepted.role == "viewer" and accepted.token_hash == hash_token(token)
    (revoked,) = await _rows(supabase_engine, data, revoked_email)
    assert revoked.revoked_at is not None and revoked.revoked_by is None


async def test_a_finished_invitation_still_cannot_be_rewritten_even_with_row_security_lifted(
    supabase_engine, owner_engine
) -> None:
    """The trigger is the second layer: policies make a finished row unreachable for updates, and
    this proves that if a path ever bypassed them the row still could not change.

    Row security is lifted for the owner role inside a transaction that is rolled back, so the
    update reaches the trigger. Only clearing the two *_by references may pass.
    """
    data = await _seed_security_tenants(supabase_engine)
    zero = data.zero_membership_id
    token = _ok(
        await _invite(supabase_engine, data, data.owner_id, _email(data, zero), "viewer")
    ).data.token
    _ok(await _accept(supabase_engine, data, zero, token))

    async def attempt(change: str) -> None:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE workspace_invitations NO FORCE ROW LEVEL SECURITY")
            )
            result = await conn.execute(
                text(f"UPDATE workspace_invitations SET {change} WHERE token_hash = :h"),
                {"h": hash_token(token)},
            )
            assert result.rowcount == 1, "the update must actually reach the row and the trigger"
            raise _Rollback

    for forbidden in (
        "role = 'owner'",
        "expires_at = expires_at + interval '1 year'",
        "accepted_at = now()",
        "accepted_by = invited_by",  # re-pointing is not the same as clearing
        "revoked_at = now()",
    ):
        with pytest.raises(DBAPIError, match="finished workspace invitation"):
            await attempt(forbidden)
    with pytest.raises(_Rollback):  # clearing the reference is the one allowed change
        await attempt("accepted_by = NULL")


class _Rollback(Exception):
    """Raised inside a transaction to roll it back after a check."""
