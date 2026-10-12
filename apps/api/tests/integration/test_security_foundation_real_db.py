"""Real Postgres verification of populated tenant isolation and revocation."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.api.v1.workspaces import add_workspace_member, revoke_workspace_member
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.dependencies import (
    AuthenticatedUser,
    require_memory_access,
    require_workspace_member,
)
from sourcemind.core.exceptions import (
    MemoryNotFoundError,
    WorkspaceAccessDeniedError,
    WorkspaceMemberExistsError,
    WorkspaceNotFoundError,
)
from sourcemind.models.memory import Memory
from sourcemind.models.organization import Organization
from sourcemind.models.user import User
from sourcemind.models.workspace import Workspace, WorkspaceMember
from sourcemind.schemas.workspace import WorkspaceMemberAdd


@dataclass(frozen=True)
class SecurityTenantData:
    owner_id: uuid.UUID
    backup_owner_id: uuid.UUID
    admin_id: uuid.UUID
    member_id: uuid.UUID
    viewer_id: uuid.UUID
    zero_membership_id: uuid.UUID
    other_workspace_member_id: uuid.UUID
    target_workspace_id: uuid.UUID
    other_workspace_id: uuid.UUID
    target_memory_id: uuid.UUID
    other_memory_id: uuid.UUID
    clerk_ids: dict[uuid.UUID, str]


class _OwnerCountBarrierSession:
    def __init__(
        self,
        session: AsyncSession,
        barrier: asyncio.Barrier,
    ) -> None:
        self._session = session
        self._barrier = barrier

    def __getattr__(self, name: str) -> object:
        return getattr(self._session, name)

    async def execute(
        self,
        statement: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        result = await self._session.execute(statement, *args, **kwargs)
        sql = str(statement)
        if (
            "workspace_members.id" in sql
            and "workspace_members.role" in sql
            and "workspace_members.user_id" not in sql
        ):
            await self._barrier.wait()
        return result


@pytest.fixture(scope="session")
def owner_race_database_url(request: pytest.FixtureRequest) -> str:
    custom_url = os.getenv("OWNER_RACE_DATABASE_URL", "")
    if not custom_url:
        return str(request.getfixturevalue("supabase_url"))

    if os.getenv("OWNER_RACE_TEST_ALLOW_DISPOSABLE") != "1":
        raise RuntimeError("owner-removal race database target is not enabled")
    expected_database = os.getenv("OWNER_RACE_EXPECTED_DATABASE", "")
    expected_port = os.getenv("OWNER_RACE_EXPECTED_PORT", "")
    if not expected_database or not expected_port:
        raise RuntimeError("owner-removal race database identity is incomplete")

    if not custom_url.startswith("postgresql+asyncpg://"):
        custom_url = custom_url.replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )
    parsed = make_url(custom_url)
    if parsed.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("refusing non-loopback owner-removal race database")
    if (
        parsed.username != "sourcemind_test"
        or parsed.database != expected_database
        or parsed.port != int(expected_port)
    ):
        raise RuntimeError("refusing unexpected owner-removal race database identity")
    return custom_url


@pytest_asyncio.fixture
async def owner_race_engine(
    owner_race_database_url: str,
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        owner_race_database_url,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_security_tenants(supabase_engine) -> SecurityTenantData:
    suffix = uuid.uuid4().hex
    owner_id = uuid.uuid4()
    backup_owner_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    member_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    zero_membership_id = uuid.uuid4()
    other_workspace_member_id = uuid.uuid4()
    target_workspace_id = uuid.uuid4()
    other_workspace_id = uuid.uuid4()

    async with AsyncSession(supabase_engine, expire_on_commit=False) as setup:
        setup.add_all(
            [
                User(id=owner_id, clerk_id=f"owner-{suffix}", email=f"owner-{suffix}@example.com"),
                User(
                    id=backup_owner_id,
                    clerk_id=f"backup-owner-{suffix}",
                    email=f"backup-owner-{suffix}@example.com",
                ),
                User(
                    id=admin_id,
                    clerk_id=f"admin-{suffix}",
                    email=f"admin-{suffix}@example.com",
                ),
                User(
                    id=member_id,
                    clerk_id=f"member-{suffix}",
                    email=f"member-{suffix}@example.com",
                ),
                User(
                    id=viewer_id,
                    clerk_id=f"viewer-{suffix}",
                    email=f"viewer-{suffix}@example.com",
                ),
                User(
                    id=zero_membership_id,
                    clerk_id=f"zero-{suffix}",
                    email=f"zero-{suffix}@example.com",
                ),
                User(
                    id=other_workspace_member_id,
                    clerk_id=f"other-{suffix}",
                    email=f"other-{suffix}@example.com",
                ),
            ]
        )
        organization = Organization(name="Security Test", slug=f"security-{suffix}")
        setup.add(organization)
        await setup.flush()
        await set_rls_user_context(setup, owner_id)

        target_workspace = Workspace(
            id=target_workspace_id,
            organization_id=organization.id,
            created_by_user_id=owner_id,
            name="Target",
            slug=f"target-{suffix}",
        )
        other_workspace = Workspace(
            id=other_workspace_id,
            organization_id=organization.id,
            created_by_user_id=owner_id,
            name="Other",
            slug=f"other-{suffix}",
        )
        await set_rls_workspace_context(setup, target_workspace.id)
        setup.add(target_workspace)
        await setup.flush()
        await set_rls_workspace_context(setup, other_workspace.id)
        setup.add(other_workspace)
        await setup.flush()

        await set_rls_workspace_context(setup, target_workspace.id)
        setup.add(
            WorkspaceMember(
                workspace_id=target_workspace.id,
                user_id=owner_id,
                role="owner",
            )
        )
        await setup.flush()
        setup.add_all(
            [
                WorkspaceMember(
                    workspace_id=target_workspace.id,
                    user_id=admin_id,
                    role="admin",
                ),
                WorkspaceMember(
                    workspace_id=target_workspace.id,
                    user_id=member_id,
                    role="member",
                ),
                WorkspaceMember(
                    workspace_id=target_workspace.id,
                    user_id=viewer_id,
                    role="viewer",
                ),
            ]
        )
        await setup.flush()
        setup.add(
            WorkspaceMember(
                workspace_id=target_workspace.id,
                user_id=backup_owner_id,
                role="owner",
            )
        )
        await setup.flush()
        target_memory = Memory(
            workspace_id=target_workspace.id,
            content="Populated tenant secret used by the security regression.",
            content_hash=hashlib.sha256(f"target-{suffix}".encode()).hexdigest(),
        )
        setup.add(target_memory)
        await setup.flush()

        await set_rls_workspace_context(setup, other_workspace.id)
        setup.add(
            WorkspaceMember(
                workspace_id=other_workspace.id,
                user_id=owner_id,
                role="owner",
            )
        )
        await setup.flush()
        setup.add(
            WorkspaceMember(
                workspace_id=other_workspace.id,
                user_id=other_workspace_member_id,
                role="member",
            )
        )
        await setup.flush()
        other_memory = Memory(
            workspace_id=other_workspace.id,
            content="Other populated tenant positive control.",
            content_hash=hashlib.sha256(f"other-{suffix}".encode()).hexdigest(),
        )
        setup.add(other_memory)
        await setup.commit()

    return SecurityTenantData(
        owner_id=owner_id,
        backup_owner_id=backup_owner_id,
        admin_id=admin_id,
        member_id=member_id,
        viewer_id=viewer_id,
        zero_membership_id=zero_membership_id,
        other_workspace_member_id=other_workspace_member_id,
        target_workspace_id=target_workspace_id,
        other_workspace_id=other_workspace_id,
        target_memory_id=target_memory.id,
        other_memory_id=other_memory.id,
        clerk_ids={
            owner_id: f"owner-{suffix}",
            backup_owner_id: f"backup-owner-{suffix}",
            admin_id: f"admin-{suffix}",
            member_id: f"member-{suffix}",
            viewer_id: f"viewer-{suffix}",
            zero_membership_id: f"zero-{suffix}",
            other_workspace_member_id: f"other-{suffix}",
        },
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_populated_memory_is_hidden_from_both_unauthorized_caller_types(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    async with AsyncSession(supabase_engine) as authorized:
        await set_rls_user_context(authorized, data.owner_id)
        await require_memory_access(authorized, data.owner_id, data.target_memory_id)
        visible = await authorized.scalar(
            select(Memory).where(Memory.id == data.target_memory_id)
        )
        assert visible is not None
        assert visible.content.startswith("Populated tenant secret")

    for unauthorized_id in (
        data.zero_membership_id,
        data.other_workspace_member_id,
    ):
        async with AsyncSession(supabase_engine) as unauthorized:
            await set_rls_user_context(unauthorized, unauthorized_id)
            with pytest.raises(MemoryNotFoundError):
                await require_memory_access(
                    unauthorized, unauthorized_id, data.target_memory_id
                )
            assert await unauthorized.scalar(
                select(Memory).where(Memory.id == data.target_memory_id)
            ) is None

    async with AsyncSession(supabase_engine) as revoke:
        await set_rls_user_context(revoke, data.owner_id)
        await set_rls_workspace_context(revoke, data.target_workspace_id)
        membership = await revoke.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.owner_id,
            )
        )
        assert membership is not None
        membership.status = "departed"
        membership.departed_at = datetime.now(UTC)
        await revoke.commit()

    async with AsyncSession(supabase_engine) as revoked:
        await set_rls_user_context(revoked, data.owner_id)
        assert await revoked.scalar(
            select(Memory).where(Memory.id == data.target_memory_id)
        ) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_context_cannot_substitute_for_membership(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    async with AsyncSession(supabase_engine) as other_member:
        await set_rls_user_context(other_member, data.other_workspace_member_id)
        await set_rls_workspace_context(other_member, data.other_workspace_id)
        own_memory = await other_member.scalar(
            select(Memory).where(Memory.id == data.other_memory_id)
        )
        assert own_memory is not None
        assert own_memory.content == "Other populated tenant positive control."

        await set_rls_workspace_context(other_member, data.target_workspace_id)
        assert (
            await other_member.scalar(
                select(text("count(*)")).select_from(Workspace)
            )
            == 0
        )
        assert (
            await other_member.scalar(
                select(text("count(*)")).select_from(WorkspaceMember)
            )
            == 0
        )
        assert await other_member.scalar(
            select(Workspace).where(Workspace.id == data.target_workspace_id)
        ) is None
        assert (
            await other_member.scalar(
                select(text("count(*)"))
                .select_from(WorkspaceMember)
                .where(WorkspaceMember.workspace_id == data.target_workspace_id)
            )
            == 0
        )
        assert await other_member.scalar(
            select(Memory).where(Memory.id == data.target_memory_id)
        ) is None

        other_member.add(
            WorkspaceMember(
                workspace_id=data.target_workspace_id,
                user_id=data.other_workspace_member_id,
                role="member",
            )
        )
        with pytest.raises(DBAPIError):
            await other_member.flush()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_workspace_insert_update_and_delete_are_blocked(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    async with AsyncSession(supabase_engine) as cross_insert:
        await set_rls_user_context(cross_insert, data.other_workspace_member_id)
        await set_rls_workspace_context(cross_insert, data.target_workspace_id)
        cross_insert.add(
            Memory(
                workspace_id=data.target_workspace_id,
                content="Cross-tenant insert must not persist.",
                content_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            )
        )
        with pytest.raises(DBAPIError):
            await cross_insert.flush()

    async with AsyncSession(supabase_engine) as cross_move:
        await set_rls_user_context(cross_move, data.other_workspace_member_id)
        await set_rls_workspace_context(cross_move, data.other_workspace_id)
        with pytest.raises(DBAPIError):
            await cross_move.execute(
                update(Memory)
                .where(Memory.id == data.other_memory_id)
                .values(workspace_id=data.target_workspace_id)
            )

    async with AsyncSession(supabase_engine) as cross_delete:
        await set_rls_user_context(cross_delete, data.other_workspace_member_id)
        await set_rls_workspace_context(cross_delete, data.target_workspace_id)
        result = await cross_delete.execute(
            delete(Memory).where(Memory.id == data.target_memory_id)
        )
        assert result.rowcount == 0
        await cross_delete.commit()

    async with AsyncSession(supabase_engine) as rewrite_membership:
        await set_rls_user_context(rewrite_membership, data.owner_id)
        await set_rls_workspace_context(
            rewrite_membership, data.target_workspace_id
        )
        with pytest.raises(DBAPIError, match="membership identity is immutable"):
            await rewrite_membership.execute(
                update(WorkspaceMember)
                .where(
                    WorkspaceMember.workspace_id == data.target_workspace_id,
                    WorkspaceMember.user_id == data.backup_owner_id,
                )
                .values(user_id=data.zero_membership_id)
            )

    async with AsyncSession(supabase_engine) as owner:
        await set_rls_user_context(owner, data.owner_id)
        target_memory = await owner.scalar(
            select(Memory).where(Memory.id == data.target_memory_id)
        )
        other_memory = await owner.scalar(
            select(Memory).where(Memory.id == data.other_memory_id)
        )
        assert target_memory is not None
        assert other_memory is not None
        assert other_memory.workspace_id == data.other_workspace_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rls_context_is_transaction_local_on_reused_connection(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    async with supabase_engine.connect() as connection:
        async with AsyncSession(bind=connection) as authorized:
            await set_rls_user_context(authorized, data.owner_id)
            assert await authorized.scalar(
                select(Memory).where(Memory.id == data.target_memory_id)
            ) is not None
            await authorized.commit()

        async with AsyncSession(bind=connection) as missing_context:
            assert await missing_context.scalar(
                select(Memory).where(Memory.id == data.target_memory_id)
            ) is None
            await missing_context.commit()

        async with AsyncSession(bind=connection) as rolled_back:
            await set_rls_user_context(rolled_back, data.owner_id)
            assert await rolled_back.scalar(
                select(Memory).where(Memory.id == data.target_memory_id)
            ) is not None
            await rolled_back.rollback()

        async with AsyncSession(bind=connection) as after_rollback:
            assert await after_rollback.scalar(
                select(Memory).where(Memory.id == data.target_memory_id)
            ) is None
            await after_rollback.execute(
                text("SELECT set_config('app.current_user_id', 'not-a-uuid', true)")
            )
            with pytest.raises(DBAPIError):
                await after_rollback.scalar(select(Memory.id).limit(1))
            await after_rollback.rollback()

        async with AsyncSession(bind=connection) as after_error:
            assert await after_error.scalar(
                select(Memory).where(Memory.id == data.target_memory_id)
            ) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_rejects_removing_the_last_active_owner(
    supabase_engine,
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    async with AsyncSession(supabase_engine) as remove_backup:
        await set_rls_user_context(remove_backup, data.owner_id)
        await set_rls_workspace_context(remove_backup, data.target_workspace_id)
        backup = await remove_backup.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.backup_owner_id,
            )
        )
        assert backup is not None
        await remove_backup.delete(backup)
        await remove_backup.commit()

    async with AsyncSession(supabase_engine) as remove_last:
        await set_rls_user_context(remove_last, data.owner_id)
        await set_rls_workspace_context(remove_last, data.target_workspace_id)
        owner = await remove_last.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.owner_id,
            )
        )
        assert owner is not None
        owner.status = "departed"
        owner.departed_at = datetime.now(UTC)
        with pytest.raises(DBAPIError, match="last active workspace owner"):
            await remove_last.flush()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_owner_revocations_preserve_one_active_owner(
    owner_race_engine,
) -> None:
    data = await _seed_security_tenants(owner_race_engine)
    barrier = asyncio.Barrier(2)

    async def remove_owner(
        actor_id: uuid.UUID,
        target_id: uuid.UUID,
    ) -> Exception | None:
        async with AsyncSession(owner_race_engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, actor_id)
            guarded_session = _OwnerCountBarrierSession(session, barrier)
            current_user = AuthenticatedUser(
                user_id=actor_id,
                clerk_id=data.clerk_ids[actor_id],
                email=f"{actor_id}@example.com",
            )
            try:
                await revoke_workspace_member(
                    workspace_id=data.target_workspace_id,
                    user_id=target_id,
                    db=guarded_session,  # type: ignore[arg-type]
                    current_user=current_user,
                    idempotency_key=str(uuid.uuid4()),
                )
            except Exception as exc:
                await session.rollback()
                return exc
            return None

    async with AsyncSession(owner_race_engine) as identity_session:
        identity = (
            await identity_session.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        assert identity == ("sourcemind_test", False, False)

    outcomes = await asyncio.gather(
        remove_owner(data.owner_id, data.backup_owner_id),
        remove_owner(data.backup_owner_id, data.owner_id),
    )

    assert sum(outcome is None for outcome in outcomes) == 1
    failures = [outcome for outcome in outcomes if outcome is not None]
    assert len(failures) == 1
    assert isinstance(failures[0], DBAPIError)
    assert "last active workspace owner" in str(failures[0])

    surviving_owner_id = (
        data.owner_id if outcomes[0] is None else data.backup_owner_id
    )
    async with AsyncSession(owner_race_engine) as verify:
        await set_rls_user_context(verify, surviving_owner_id)
        await set_rls_workspace_context(verify, data.target_workspace_id)
        active_owner_count = await verify.scalar(
            select(text("count(*)"))
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.role == "owner",
                WorkspaceMember.status == "active",
                WorkspaceMember.departed_at.is_(None),
            )
        )
        assert active_owner_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_workspace_policies_require_active_user_access(
    supabase_engine,
) -> None:
    protected_tables = {
        "artifact_links",
        "attribution_edits",
        "attributions",
        "connector_configs",
        "connector_sync_logs",
        "documents",
        "handoff_assignments",
        "handoff_records",
        "memories",
        "memory_conflicts",
        "memory_relations",
        "workspace_members",
        "workspaces",
    }
    async with AsyncSession(supabase_engine) as session:
        for internal_table in (
            "sm_workspace_bootstraps",
            "sm_workspace_access_grants",
        ):
            has_access = await session.scalar(
                text(
                    "SELECT has_table_privilege(current_user, :table, "
                    "'SELECT,INSERT,UPDATE,DELETE')"
                ),
                {"table": internal_table},
            )
            assert has_access is False

        with pytest.raises(DBAPIError, match="permission denied"):
            await session.scalar(text("SELECT count(*) FROM sm_workspace_access_grants"))
        await session.rollback()

        rows = (
            await session.execute(
                text(
                    "SELECT tablename, policyname, qual, with_check "
                    "FROM pg_policies WHERE schemaname = 'public' "
                    "AND tablename = ANY(CAST(:tables AS text[]))"
                ),
                {"tables": sorted(protected_tables)},
            )
        ).all()

    assert {row.tablename for row in rows} == protected_tables
    for row in rows:
        expression = " ".join(filter(None, (row.qual, row.with_check)))
        if row.tablename in {"workspaces", "workspace_members"}:
            assert any(
                guard in expression
                for guard in (
                    "sm_has_active_workspace_access",
                    "sm_can_bootstrap_workspace_owner",
                    "sm_is_workspace_creator_without_owner",
                    "created_by_user_id",
                )
            )
        else:
            assert "workspace_members" in expression
            assert "current_user_id" in expression


# ── Owner/admin adds an ordinary member by email (D-024) ─────────────────────
#
# The handler is called directly with real sessions on the restricted runtime
# role, so RLS, row locks and triggers are real. Only the Clerk lookup and the
# Redis rate limiter are replaced (no network in tests).


@pytest.fixture
def member_add_env():
    settings = SimpleNamespace(auth_dev_bypass_enabled=False, clerk_secret_key="sk_test_x")

    async def verified_clerk(clerk_id: str, _secret: str) -> tuple[str, str]:
        return f"{clerk_id}@example.com", "verified"

    with (
        patch("sourcemind.api.v1.workspaces.enforce_rate_limit", new=AsyncMock()),
        patch("sourcemind.api.v1.workspaces.get_settings", return_value=settings),
        patch(
            "sourcemind.api.v1.workspaces.fetch_clerk_primary_email_verification",
            new=verified_clerk,
        ),
    ):
        yield


class _StageSession:
    """Delegating session that pauses at named points of add_workspace_member."""

    def __init__(
        self,
        session,
        *,
        before_caller_lock=None,
        after_caller_lock=None,
        after_target_select=None,
    ) -> None:
        self._session = session
        self._before_caller_lock = before_caller_lock
        self._after_caller_lock = after_caller_lock
        self._after_target_select = after_target_select

    def __getattr__(self, name: str) -> object:
        return getattr(self._session, name)

    async def execute(self, statement, *args, **kwargs):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        caller_lock = "FOR SHARE" in sql
        if caller_lock and self._before_caller_lock:
            await self._before_caller_lock()
        result = await self._session.execute(statement, *args, **kwargs)
        if caller_lock and self._after_caller_lock:
            await self._after_caller_lock()
        if (
            self._after_target_select
            and "FOR UPDATE" in sql
            and "workspace_members.id" in sql
        ):
            await self._after_target_select()
        return result


def _email(data: SecurityTenantData, user_id: uuid.UUID) -> str:
    return f"{data.clerk_ids[user_id]}@example.com"


def _actor(data: SecurityTenantData, user_id: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id, clerk_id=data.clerk_ids[user_id], email=_email(data, user_id)
    )


async def _add(engine, data, actor_id, target_id, role="member", **stages):
    """Run add_workspace_member; return the response or the raised exception."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, actor_id)
        db = _StageSession(session, **stages) if stages else session
        try:
            return await add_workspace_member(
                workspace_id=data.target_workspace_id,
                body=WorkspaceMemberAdd(email=_email(data, target_id), role=role),
                db=db,  # type: ignore[arg-type]
                current_user=_actor(data, actor_id),
                request_id="test",
                idempotency_key=str(uuid.uuid4()),
            )
        except Exception as exc:
            await session.rollback()
            return exc


async def _revoke(engine, data, actor_id, target_id):
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await set_rls_user_context(session, actor_id)
        try:
            await revoke_workspace_member(
                workspace_id=data.target_workspace_id,
                user_id=target_id,
                db=session,
                current_user=_actor(data, actor_id),
                idempotency_key=str(uuid.uuid4()),
            )
        except Exception as exc:
            await session.rollback()
            return exc
        return None


async def _membership(engine, data, user_id):
    """The user's membership row, read through the first owner."""
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        rows = (
            (
                await session.execute(
                    select(WorkspaceMember).where(
                        WorkspaceMember.workspace_id == data.target_workspace_id,
                        WorkspaceMember.user_id == user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) <= 1
        return rows[0] if rows else None


async def _is_active_member(engine, data, user_id) -> bool:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, user_id)
        try:
            await require_workspace_member(session, user_id, data.target_workspace_id)
        except WorkspaceNotFoundError:
            return False
        return True


async def _has_roles(engine, data, user_id, roles: list[str]) -> bool:
    """Ask the access-grants shadow table (via its policy function) as the user."""
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, user_id)
        return bool(
            await session.scalar(
                text(
                    "SELECT sm_has_active_workspace_access("
                    "CAST(:ws AS uuid), CAST(:roles AS text[]))"
                ),
                {"ws": str(data.target_workspace_id), "roles": roles},
            )
        )


async def _count_roles(engine, data, roles: tuple[str, ...]) -> int:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        return await session.scalar(
            select(text("count(*)"))
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.role.in_(roles),
                WorkspaceMember.status == "active",
            )
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_adds_signed_in_member_who_can_then_read_the_workspace(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    assert not await _is_active_member(supabase_engine, data, data.zero_membership_id)

    added = await _add(supabase_engine, data, data.admin_id, data.zero_membership_id)

    assert not isinstance(added, Exception), added
    assert added.data.role == "member"
    assert added.data.user.id == data.zero_membership_id
    row = await _membership(supabase_engine, data, data.zero_membership_id)
    assert (row.role, row.status, row.departed_at) == ("member", "active", None)
    assert row.invited_by_id == data.admin_id
    assert await _is_active_member(supabase_engine, data, data.zero_membership_id)
    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.zero_membership_id)
        await require_memory_access(
            session, data.zero_membership_id, data.target_memory_id
        )
    assert await _has_roles(supabase_engine, data, data.zero_membership_id, ["member"])
    assert not await _has_roles(
        supabase_engine, data, data.zero_membership_id, ["admin", "owner"]
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_member_viewer_and_other_workspace_admin_cannot_add(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    for caller_id in (data.member_id, data.viewer_id):
        outcome = await _add(supabase_engine, data, caller_id, data.zero_membership_id)
        assert isinstance(outcome, WorkspaceAccessDeniedError), outcome
    for caller_id in (data.zero_membership_id, data.other_workspace_member_id):
        outcome = await _add(supabase_engine, data, caller_id, data.viewer_id)
        assert isinstance(outcome, WorkspaceNotFoundError), outcome

    # An administrator of the OTHER workspace has no standing in this one.
    async with AsyncSession(supabase_engine) as promote:
        await set_rls_user_context(promote, data.owner_id)
        await set_rls_workspace_context(promote, data.other_workspace_id)
        await promote.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == data.other_workspace_id,
                WorkspaceMember.user_id == data.other_workspace_member_id,
            )
            .values(role="admin")
        )
        await promote.commit()
    outcome = await _add(
        supabase_engine,
        data,
        data.other_workspace_member_id,
        data.zero_membership_id,
    )
    assert isinstance(outcome, WorkspaceNotFoundError), outcome
    assert await _membership(supabase_engine, data, data.zero_membership_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_double_add_of_one_user_yields_one_201_and_one_409(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    barrier = asyncio.Barrier(2)

    outcomes = await asyncio.gather(
        _add(
            supabase_engine,
            data,
            data.owner_id,
            data.zero_membership_id,
            after_target_select=barrier.wait,
        ),
        _add(
            supabase_engine,
            data,
            data.admin_id,
            data.zero_membership_id,
            after_target_select=barrier.wait,
        ),
    )

    created = [o for o in outcomes if not isinstance(o, Exception)]
    refused = [o for o in outcomes if isinstance(o, Exception)]
    assert len(created) == 1, outcomes
    assert len(refused) == 1
    assert isinstance(refused[0], WorkspaceMemberExistsError)
    assert refused[0].details == {"reason": "already_member"}
    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        count = await session.scalar(
            select(text("count(*)"))
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.zero_membership_id,
            )
        )
    assert count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_departed_former_admin_readded_as_member_cannot_administer(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    assert await _revoke(supabase_engine, data, data.owner_id, data.admin_id) is None
    departed = await _membership(supabase_engine, data, data.admin_id)
    assert (departed.role, departed.status) == ("admin", "departed")
    assert not await _has_roles(supabase_engine, data, data.admin_id, ["admin", "member"])

    readded = await _add(supabase_engine, data, data.owner_id, data.admin_id, "member")

    assert not isinstance(readded, Exception), readded
    row = await _membership(supabase_engine, data, data.admin_id)
    assert (row.role, row.status, row.departed_at) == ("member", "active", None)
    assert row.invited_by_id == data.owner_id
    # The access-grants shadow row (synced by trigger) agrees: member, not admin.
    assert await _has_roles(supabase_engine, data, data.admin_id, ["member"])
    assert not await _has_roles(supabase_engine, data, data.admin_id, ["admin", "owner"])
    # ... and the old privileges are really gone.
    denied_add = await _add(supabase_engine, data, data.admin_id, data.zero_membership_id)
    assert isinstance(denied_add, WorkspaceAccessDeniedError), denied_add
    denied_revoke = await _revoke(supabase_engine, data, data.admin_id, data.member_id)
    assert isinstance(denied_revoke, WorkspaceAccessDeniedError), denied_revoke
    assert await _membership(supabase_engine, data, data.zero_membership_id) is None
    assert (await _membership(supabase_engine, data, data.member_id)).status == "active"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_revoke_then_readd_restores_access_immediately(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)

    assert await _revoke(supabase_engine, data, data.admin_id, data.member_id) is None
    assert not await _is_active_member(supabase_engine, data, data.member_id)
    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.member_id)
        with pytest.raises(MemoryNotFoundError):
            await require_memory_access(session, data.member_id, data.target_memory_id)

    readded = await _add(supabase_engine, data, data.admin_id, data.member_id, "viewer")

    assert not isinstance(readded, Exception), readded
    assert readded.data.role == "viewer"
    assert await _is_active_member(supabase_engine, data, data.member_id)
    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.member_id)
        await require_memory_access(session, data.member_id, data.target_memory_id)
    # Already active again: a second add is a conflict, not a second row.
    again = await _add(supabase_engine, data, data.admin_id, data.member_id)
    assert isinstance(again, WorkspaceMemberExistsError)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_revoke_of_the_caller_committing_before_insert_refuses_the_add(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    reached = asyncio.Event()
    release = asyncio.Event()

    async def pause_before_caller_lock() -> None:
        reached.set()
        await release.wait()

    add_task = asyncio.create_task(
        _add(
            supabase_engine,
            data,
            data.admin_id,
            data.zero_membership_id,
            before_caller_lock=pause_before_caller_lock,
        )
    )
    await asyncio.wait_for(reached.wait(), 15)
    # The add has passed its permission check but holds no lock yet.
    assert await _revoke(supabase_engine, data, data.owner_id, data.admin_id) is None
    release.set()
    outcome = await asyncio.wait_for(add_task, 15)

    assert isinstance(outcome, (WorkspaceNotFoundError, WorkspaceAccessDeniedError)), outcome
    assert await _membership(supabase_engine, data, data.zero_membership_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_add_holding_the_caller_share_lock_makes_the_revoke_wait_then_both_succeed(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    locked = asyncio.Event()
    proceed = asyncio.Event()

    async def hold_caller_lock() -> None:
        locked.set()
        await proceed.wait()

    add_task = asyncio.create_task(
        _add(
            supabase_engine,
            data,
            data.admin_id,
            data.zero_membership_id,
            after_caller_lock=hold_caller_lock,
        )
    )
    await asyncio.wait_for(locked.wait(), 15)
    revoke_task = asyncio.create_task(
        _revoke(supabase_engine, data, data.owner_id, data.admin_id)
    )
    done, _pending = await asyncio.wait({revoke_task}, timeout=1.5)
    assert not done, "revoke of the caller must wait for the add's FOR SHARE lock"

    proceed.set()
    added = await asyncio.wait_for(add_task, 15)
    revoked = await asyncio.wait_for(revoke_task, 15)

    assert not isinstance(added, Exception), added
    assert revoked is None
    assert (await _membership(supabase_engine, data, data.zero_membership_id)).status == "active"
    assert (await _membership(supabase_engine, data, data.admin_id)).status == "departed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_last_owner_stays_protected_and_no_owner_or_admin_row_is_creatable(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    privileged_before = await _count_roles(supabase_engine, data, ("owner", "admin"))

    for role in ("owner", "admin"):
        with pytest.raises(ValueError):
            WorkspaceMemberAdd(email=_email(data, data.zero_membership_id), role=role)
    with pytest.raises(ValueError):
        WorkspaceMemberAdd(
            email=_email(data, data.zero_membership_id),
            user_id=str(data.zero_membership_id),
        )

    for target, role in (
        (data.zero_membership_id, "member"),
        (data.other_workspace_member_id, "viewer"),
    ):
        added = await _add(supabase_engine, data, data.admin_id, target, role)
        assert not isinstance(added, Exception), added
    assert await _count_roles(supabase_engine, data, ("owner", "admin")) == privileged_before

    assert await _revoke(supabase_engine, data, data.owner_id, data.backup_owner_id) is None
    refused = await _revoke(supabase_engine, data, data.owner_id, data.owner_id)
    assert isinstance(refused, WorkspaceAccessDeniedError)
    async with AsyncSession(supabase_engine) as remove_last:
        await set_rls_user_context(remove_last, data.owner_id)
        await set_rls_workspace_context(remove_last, data.target_workspace_id)
        owner = await remove_last.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.owner_id,
            )
        )
        owner.status = "departed"
        owner.departed_at = datetime.now(UTC)
        with pytest.raises(DBAPIError, match="last active workspace owner"):
            await remove_last.flush()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_only_an_owner_can_readd_a_former_owner_and_always_as_an_ordinary_role(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    assert await _revoke(supabase_engine, data, data.owner_id, data.backup_owner_id) is None

    by_admin = await _add(supabase_engine, data, data.admin_id, data.backup_owner_id)

    assert isinstance(by_admin, WorkspaceAccessDeniedError), by_admin
    assert "former owner" in by_admin.message
    row = await _membership(supabase_engine, data, data.backup_owner_id)
    assert (row.role, row.status) == ("owner", "departed")

    by_owner = await _add(supabase_engine, data, data.owner_id, data.backup_owner_id)

    assert not isinstance(by_owner, Exception), by_owner
    row = await _membership(supabase_engine, data, data.backup_owner_id)
    assert (row.role, row.status, row.departed_at) == ("member", "active", None)
    assert not await _has_roles(supabase_engine, data, data.backup_owner_id, ["admin", "owner"])


@pytest_asyncio.fixture
async def wide_engine(supabase_url: str) -> AsyncIterator[AsyncEngine]:
    """Enough pooled connections for four overlapping requests (default pool is 2)."""
    engine = create_async_engine(supabase_url, pool_size=8, max_overflow=0, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reciprocal_and_self_adds_never_deadlock_or_return_500(
    wide_engine, member_add_env
) -> None:
    supabase_engine = wide_engine
    data = await _seed_security_tenants(supabase_engine)
    # Two ADMINISTRATORS: RLS hides an owner's row from an admin's FOR UPDATE, so
    # an owner/admin pair can never form the cycle. Promote the member to admin.
    async with AsyncSession(supabase_engine) as promote:
        await set_rls_user_context(promote, data.owner_id)
        await set_rls_workspace_context(promote, data.target_workspace_id)
        await promote.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.member_id,
            )
            .values(role="admin")
        )
        await promote.commit()
    peer_id = data.member_id

    async def linger() -> None:
        # Long enough that, with unordered locking, each request would hold its
        # own caller row FOR SHARE before the other asked for a FOR UPDATE.
        await asyncio.sleep(0.6)

    pairs = [
        (data.admin_id, peer_id),
        (peer_id, data.admin_id),
        (data.admin_id, data.admin_id),
        (peer_id, peer_id),
    ]
    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *(
                _add(supabase_engine, data, actor, target, after_caller_lock=linger)
                for actor, target in pairs
            )
        ),
        60,
    )

    for outcome in outcomes:
        assert isinstance(outcome, WorkspaceMemberExistsError), outcome
        assert outcome.details == {"reason": "already_member"}
    # Nothing changed: both administrators are still active administrators.
    assert (await _membership(supabase_engine, data, data.admin_id)).role == "admin"
    assert (await _membership(supabase_engine, data, peer_id)).role == "admin"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_admins_readding_the_same_departed_members_yield_one_winner_each(
    wide_engine, member_add_env
) -> None:
    supabase_engine = wide_engine
    data = await _seed_security_tenants(supabase_engine)
    # Two departed members, each targeted by BOTH administrators at once. One add
    # per member wins; the other is a clean already_member conflict.
    assert await _revoke(supabase_engine, data, data.owner_id, data.member_id) is None
    assert await _revoke(supabase_engine, data, data.owner_id, data.viewer_id) is None

    async def linger() -> None:
        await asyncio.sleep(0.4)

    outcomes = await asyncio.wait_for(
        asyncio.gather(
            _add(supabase_engine, data, data.owner_id, data.member_id, after_caller_lock=linger),
            _add(supabase_engine, data, data.admin_id, data.viewer_id, after_caller_lock=linger),
            _add(supabase_engine, data, data.admin_id, data.member_id, after_caller_lock=linger),
            _add(supabase_engine, data, data.owner_id, data.viewer_id, after_caller_lock=linger),
        ),
        60,
    )

    for outcome in outcomes:
        assert not isinstance(outcome, Exception) or isinstance(
            outcome, WorkspaceMemberExistsError
        ), outcome
    # Each departed member was re-added exactly once.
    assert sum(not isinstance(o, Exception) for o in outcomes) == 2
    assert (await _membership(supabase_engine, data, data.member_id)).status == "active"
    assert (await _membership(supabase_engine, data, data.viewer_id)).status == "active"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_member_in_a_handoff_cannot_be_readded_until_it_finishes(
    supabase_engine, member_add_env
) -> None:
    data = await _seed_security_tenants(supabase_engine)
    async with AsyncSession(supabase_engine) as session:
        await set_rls_user_context(session, data.owner_id)
        await set_rls_workspace_context(session, data.target_workspace_id)
        await session.execute(
            update(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == data.target_workspace_id,
                WorkspaceMember.user_id == data.member_id,
            )
            .values(status="departing")
        )
        await session.commit()

    refused = await _add(supabase_engine, data, data.admin_id, data.member_id)

    assert isinstance(refused, WorkspaceMemberExistsError), refused
    assert refused.details == {"reason": "handoff_in_progress"}
    row = await _membership(supabase_engine, data, data.member_id)
    assert (row.role, row.status) == ("member", "departing")
