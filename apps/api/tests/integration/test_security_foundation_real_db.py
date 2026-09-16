"""Real Postgres verification of populated tenant isolation and revocation."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.dependencies import require_memory_access
from sourcemind.core.exceptions import MemoryNotFoundError
from sourcemind.models.memory import Memory
from sourcemind.models.organization import Organization
from sourcemind.models.user import User
from sourcemind.models.workspace import Workspace, WorkspaceMember


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
