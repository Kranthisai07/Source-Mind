"""Real Postgres verification of populated tenant isolation and revocation."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.dependencies import require_memory_access
from sourcemind.core.exceptions import MemoryNotFoundError
from sourcemind.models.memory import Memory
from sourcemind.models.organization import Organization
from sourcemind.models.user import User
from sourcemind.models.workspace import Workspace, WorkspaceMember


@pytest.mark.integration
@pytest.mark.asyncio
async def test_populated_memory_is_hidden_from_both_unauthorized_caller_types(
    supabase_engine,
) -> None:
    suffix = uuid.uuid4().hex
    owner_id = uuid.uuid4()
    zero_membership_id = uuid.uuid4()
    other_workspace_member_id = uuid.uuid4()

    async with AsyncSession(supabase_engine, expire_on_commit=False) as setup:
        setup.add_all(
            [
                User(id=owner_id, clerk_id=f"owner-{suffix}", email=f"owner-{suffix}@test.local"),
                User(
                    id=zero_membership_id,
                    clerk_id=f"zero-{suffix}",
                    email=f"zero-{suffix}@test.local",
                ),
                User(
                    id=other_workspace_member_id,
                    clerk_id=f"other-{suffix}",
                    email=f"other-{suffix}@test.local",
                ),
            ]
        )
        organization = Organization(name="Security Test", slug=f"security-{suffix}")
        setup.add(organization)
        await setup.flush()
        await set_rls_user_context(setup, owner_id)

        target_workspace = Workspace(
            id=uuid.uuid4(),
            organization_id=organization.id,
            name="Target",
            slug=f"target-{suffix}",
        )
        other_workspace = Workspace(
            id=uuid.uuid4(),
            organization_id=organization.id,
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
        populated_memory = Memory(
            workspace_id=target_workspace.id,
            content="Populated tenant secret used by the security regression.",
            content_hash=hashlib.sha256(suffix.encode()).hexdigest(),
        )
        setup.add(populated_memory)
        await setup.flush()

        await set_rls_workspace_context(setup, other_workspace.id)
        setup.add(
            WorkspaceMember(
                workspace_id=other_workspace.id,
                user_id=other_workspace_member_id,
                role="member",
            )
        )
        await setup.commit()

    async with AsyncSession(supabase_engine) as authorized:
        await set_rls_user_context(authorized, owner_id)
        await require_memory_access(authorized, owner_id, populated_memory.id)
        visible = await authorized.scalar(
            select(Memory).where(Memory.id == populated_memory.id)
        )
        assert visible is not None
        assert visible.content.startswith("Populated tenant secret")

    for unauthorized_id in (zero_membership_id, other_workspace_member_id):
        async with AsyncSession(supabase_engine) as unauthorized:
            await set_rls_user_context(unauthorized, unauthorized_id)
            with pytest.raises(MemoryNotFoundError):
                await require_memory_access(
                    unauthorized, unauthorized_id, populated_memory.id
                )
            assert await unauthorized.scalar(
                select(Memory).where(Memory.id == populated_memory.id)
            ) is None

    async with AsyncSession(supabase_engine) as revoke:
        await set_rls_user_context(revoke, owner_id)
        await set_rls_workspace_context(revoke, target_workspace.id)
        membership = await revoke.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == target_workspace.id,
                WorkspaceMember.user_id == owner_id,
            )
        )
        assert membership is not None
        membership.status = "departed"
        membership.departed_at = datetime.now(UTC)
        await revoke.commit()

    async with AsyncSession(supabase_engine) as revoked:
        await set_rls_user_context(revoked, owner_id)
        assert await revoked.scalar(
            select(Memory).where(Memory.id == populated_memory.id)
        ) is None
