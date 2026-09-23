"""Regression coverage for legacy ownerless workspaces upgraded through 0008."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context

ACTIVE_ADMIN_ID = uuid.UUID("81000000-0000-4000-8000-000000000011")
ACTIVE_MEMBER_ID = uuid.UUID("81000000-0000-4000-8000-000000000012")
CONTROL_OWNER_ID = uuid.UUID("81000000-0000-4000-8000-000000000013")
DEPARTED_MEMBER_ID = uuid.UUID("81000000-0000-4000-8000-000000000014")
DELETED_ADMIN_ID = uuid.UUID("81000000-0000-4000-8000-000000000015")
OUTSIDER_ID = uuid.UUID("81000000-0000-4000-8000-000000000016")

OWNERLESS_WORKSPACE_ID = uuid.UUID("82000000-0000-4000-8000-000000000001")
CONTROL_WORKSPACE_ID = uuid.UUID("82000000-0000-4000-8000-000000000002")
DELETED_WORKSPACE_ID = uuid.UUID("82000000-0000-4000-8000-000000000003")


@pytest.fixture(scope="session")
def ownerless_compat_database_url() -> str:
    if os.getenv("OWNERLESS_COMPAT_TEST_ALLOW_DISPOSABLE") != "1":
        pytest.skip("ownerless migration compatibility target is not enabled")

    url = os.getenv("OWNERLESS_COMPAT_DATABASE_URL", "")
    expected_database = os.getenv("OWNERLESS_COMPAT_EXPECTED_DATABASE", "")
    if not url or not expected_database:
        raise RuntimeError("ownerless compatibility database configuration is incomplete")
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("ownerless compatibility test requires an asyncpg URL")

    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    database = parsed.path.removeprefix("/")
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("refusing non-loopback ownerless compatibility database")
    if parsed.username != "sourcemind_test" or database != expected_database:
        raise RuntimeError("refusing unexpected ownerless compatibility database identity")
    return url


@pytest_asyncio.fixture
async def ownerless_compat_engine(
    ownerless_compat_database_url: str,
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(ownerless_compat_database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _visibility_counts(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> tuple[int, int, int]:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, user_id)
        await set_rls_workspace_context(session, workspace_id)
        parameters = {"workspace_id": str(workspace_id)}
        workspace_count = await session.scalar(
            text(
                "SELECT count(*) FROM workspaces "
                "WHERE id = CAST(:workspace_id AS uuid)"
            ),
            parameters,
        )
        membership_count = await session.scalar(
            text(
                "SELECT count(*) FROM workspace_members "
                "WHERE workspace_id = CAST(:workspace_id AS uuid)"
            ),
            parameters,
        )
        document_count = await session.scalar(
            text(
                "SELECT count(*) FROM documents "
                "WHERE workspace_id = CAST(:workspace_id AS uuid)"
            ),
            parameters,
        )
        return (
            int(workspace_count or 0),
            int(membership_count or 0),
            int(document_count or 0),
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ownerless_workspace_membership_survives_migration_round_trip(
    ownerless_compat_engine: AsyncEngine,
) -> None:
    async with AsyncSession(ownerless_compat_engine) as role_session:
        role = (
            await role_session.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            )
        ).one()
        assert role == (False, False)

    assert await _visibility_counts(
        ownerless_compat_engine, ACTIVE_ADMIN_ID, OWNERLESS_WORKSPACE_ID
    ) == (1, 3, 1)
    assert await _visibility_counts(
        ownerless_compat_engine, ACTIVE_MEMBER_ID, OWNERLESS_WORKSPACE_ID
    ) == (1, 3, 1)
    assert await _visibility_counts(
        ownerless_compat_engine, CONTROL_OWNER_ID, CONTROL_WORKSPACE_ID
    ) == (1, 1, 1)

    assert await _visibility_counts(
        ownerless_compat_engine, ACTIVE_ADMIN_ID, CONTROL_WORKSPACE_ID
    ) == (0, 0, 0)
    assert await _visibility_counts(
        ownerless_compat_engine, CONTROL_OWNER_ID, OWNERLESS_WORKSPACE_ID
    ) == (0, 0, 0)
    assert await _visibility_counts(
        ownerless_compat_engine, DEPARTED_MEMBER_ID, OWNERLESS_WORKSPACE_ID
    ) == (0, 0, 0)
    assert await _visibility_counts(
        ownerless_compat_engine, DELETED_ADMIN_ID, DELETED_WORKSPACE_ID
    ) == (0, 0, 0)

    async with AsyncSession(ownerless_compat_engine) as claim_session:
        await set_rls_user_context(claim_session, ACTIVE_ADMIN_ID)
        await set_rls_workspace_context(claim_session, OWNERLESS_WORKSPACE_ID)
        creator_id = await claim_session.scalar(
            text(
                "SELECT created_by_user_id FROM workspaces "
                "WHERE id = CAST(:workspace_id AS uuid)"
            ),
            {"workspace_id": str(OWNERLESS_WORKSPACE_ID)},
        )
        assert creator_id is None

        with pytest.raises(DBAPIError, match="row-level security"):
            await claim_session.execute(
                text(
                    "INSERT INTO workspace_members "
                    "(id, workspace_id, user_id, role, status, departed_at) "
                    "VALUES "
                    "(CAST(:membership_id AS uuid), CAST(:workspace_id AS uuid), "
                    " CAST(:user_id AS uuid), 'owner', 'active', NULL)"
                ),
                {
                    "membership_id": "83000000-0000-4000-8000-000000000099",
                    "workspace_id": str(OWNERLESS_WORKSPACE_ID),
                    "user_id": str(OUTSIDER_ID),
                },
            )
        await claim_session.rollback()

    async with AsyncSession(ownerless_compat_engine) as verify_session:
        await set_rls_user_context(verify_session, ACTIVE_ADMIN_ID)
        await set_rls_workspace_context(verify_session, OWNERLESS_WORKSPACE_ID)
        owner_count = await verify_session.scalar(
            text(
                "SELECT count(*) FROM workspace_members "
                "WHERE workspace_id = CAST(:workspace_id AS uuid) "
                "AND role = 'owner'"
            ),
            {"workspace_id": str(OWNERLESS_WORKSPACE_ID)},
        )
        assert owner_count == 0
