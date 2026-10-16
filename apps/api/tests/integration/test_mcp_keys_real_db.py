"""MCP API keys against PostgreSQL as the restricted runtime role."""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.api.v1 import mcp_keys as api
from sourcemind.core.database import (
    get_db_session,
    set_rls_user_context,
    set_rls_workspace_context,
)
from sourcemind.core.dependencies import AuthenticatedUser, get_current_user
from sourcemind.core.exceptions import RateLimitExceededError, WorkspaceNotFoundError
from sourcemind.schemas.mcp_keys import McpKeyCreate
from sourcemind.services import mcp_keys
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="session")
def owner_database_url() -> str:
    if os.getenv("SECURITY_TEST_ALLOW_DISPOSABLE") != "1":
        pytest.skip("Set SECURITY_TEST_ALLOW_DISPOSABLE=1 to use the disposable test DB")
    url = os.getenv("MIGRATION_DATABASE_URL", "")
    if not url:
        pytest.skip("MIGRATION_DATABASE_URL (disposable owner role) is not configured")
    if not url.startswith("postgresql+asyncpg"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    parsed = make_url(url)
    if (
        parsed.host not in {"127.0.0.1", "localhost"}
        or parsed.port != 55432
        or parsed.username != "sourcemind_owner"
        or parsed.database != "sourcemind_security_test"
    ):
        raise RuntimeError("refusing non-disposable MIGRATION_DATABASE_URL")
    return url


@pytest_asyncio.fixture
async def owner_engine(owner_database_url: str):
    engine = create_async_engine(owner_database_url, pool_size=1, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def tenants(supabase_engine: AsyncEngine) -> SecurityTenantData:
    return await _seed_security_tenants(supabase_engine)


def _user(user_id: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(user_id, f"clerk-{user_id}", f"{user_id}@example.com")


async def _session(
    engine: AsyncEngine,
    user_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    if user_id is not None:
        await set_rls_user_context(session, user_id)
    if workspace_id is not None:
        await set_rls_workspace_context(session, workspace_id)
    return session


async def _create(
    engine: AsyncEngine,
    data: SecurityTenantData,
    user_id: uuid.UUID,
    name: str = "CLI key",
) -> mcp_keys.CreatedKey:
    async with await _session(engine, user_id, data.target_workspace_id) as session:
        created = await mcp_keys.create_key(
            session,
            user_id=user_id,
            workspace_id=data.target_workspace_id,
            name=name,
            expires_in_days=90,
        )
        await session.commit()
        return created


async def _set_lookup(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT set_config('app.mcp_key_hash', CAST(:key_hash AS text), true)"),
        {"key_hash": mcp_keys.hash_key(key)},
    )


async def test_create_returns_secret_once_and_storage_and_list_never_do(
    supabase_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    created = await _create(supabase_engine, tenants, tenants.member_id)
    assert created.key.startswith("smk_") and created.key_prefix == created.key[:8]

    async with await _session(
        supabase_engine, tenants.admin_id, tenants.target_workspace_id
    ) as admin:
        stored = (
            await admin.execute(
                text("SELECT * FROM mcp_api_keys WHERE id = CAST(:key_id AS uuid)"),
                {"key_id": str(created.id)},
            )
        ).mappings().one()
        listed = await mcp_keys.list_keys(
            admin,
            user_id=tenants.admin_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=True,
        )

    assert stored["key_hash"] == mcp_keys.hash_key(created.key)
    assert created.key not in " ".join(str(value) for value in stored.values())
    assert created.key not in repr(listed)
    assert mcp_keys.hash_key(created.key) not in repr(listed)
    assert not hasattr(listed[0], "key_hash") and not hasattr(listed[0], "key")


async def test_lookup_policy_and_resolve_expose_only_one_live_matching_key(
    supabase_engine: AsyncEngine,
    owner_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    live = await _create(supabase_engine, tenants, tenants.member_id, "live")
    second = await _create(supabase_engine, tenants, tenants.member_id, "second")
    expired = await _create(supabase_engine, tenants, tenants.member_id, "expired")

    async with await _session(
        owner_engine, tenants.owner_id, tenants.target_workspace_id
    ) as owner:
        await owner.execute(text("ALTER TABLE mcp_api_keys NO FORCE ROW LEVEL SECURITY"))
        await owner.execute(
            text(
                "UPDATE mcp_api_keys SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day' WHERE id = CAST(:key_id AS uuid)"
            ),
            {"key_id": str(expired.id)},
        )
        await owner.execute(text("ALTER TABLE mcp_api_keys FORCE ROW LEVEL SECURITY"))
        await owner.commit()

    async with await _session(supabase_engine) as contextless:
        assert (await contextless.execute(text("SELECT * FROM mcp_api_keys"))).all() == []
        await contextless.execute(text("SELECT set_config('app.mcp_key_hash', '', true)"))
        assert (await contextless.execute(text("SELECT * FROM mcp_api_keys"))).all() == []
        await _set_lookup(contextless, live.key)
        visible = (await contextless.execute(text("SELECT id FROM mcp_api_keys"))).scalars().all()
        assert visible == [live.id]
        await contextless.execute(
            text("SELECT set_config('app.mcp_key_hash', :wrong_hash, true)"),
            {"wrong_hash": "0" * 64},
        )
        assert (await contextless.execute(text("SELECT * FROM mcp_api_keys"))).all() == []

    async with await _session(supabase_engine) as resolver:
        assert (await mcp_keys.resolve_key(resolver, live.key)).key_id == live.id  # type: ignore[union-attr]
        assert (await mcp_keys.resolve_key(resolver, second.key)).key_id == second.id  # type: ignore[union-attr]
        assert await mcp_keys.resolve_key(resolver, "smk_" + "x" * 43) is None
        assert await mcp_keys.resolve_key(resolver, expired.key) is None


async def test_revoke_is_immediate_idempotent_and_writes_one_event(
    supabase_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    created = await _create(supabase_engine, tenants, tenants.member_id)
    async with await _session(
        supabase_engine, tenants.member_id, tenants.target_workspace_id
    ) as member:
        first = await mcp_keys.revoke_key(
            member,
            key_id=created.id,
            actor_user_id=tenants.member_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=False,
        )
        second = await mcp_keys.revoke_key(
            member,
            key_id=created.id,
            actor_user_id=tenants.member_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=False,
        )
        await member.commit()
    assert first is not None and second is not None
    assert first.revoked_at == second.revoked_at

    async with await _session(supabase_engine) as resolver:
        assert await mcp_keys.resolve_key(resolver, created.key) is None
    async with await _session(
        supabase_engine, tenants.admin_id, tenants.target_workspace_id
    ) as admin:
        events = (
            await admin.execute(
                text(
                    "SELECT event, actor_user_id FROM mcp_key_events "
                    "WHERE key_id = CAST(:key_id AS uuid) ORDER BY id"
                ),
                {"key_id": str(created.id)},
            )
        ).all()
    assert events == [("created", tenants.member_id), ("revoked", tenants.member_id)]


async def test_member_admin_and_workspace_authorization_are_indistinguishable(
    supabase_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    member_key = await _create(supabase_engine, tenants, tenants.member_id, "member")
    viewer_key = await _create(supabase_engine, tenants, tenants.viewer_id, "viewer")

    async with await _session(
        supabase_engine, tenants.member_id, tenants.target_workspace_id
    ) as member:
        own = await mcp_keys.list_keys(
            member,
            user_id=tenants.member_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=False,
        )
        denied = await mcp_keys.revoke_key(
            member,
            key_id=viewer_key.id,
            actor_user_id=tenants.member_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=False,
        )
    assert {item.id for item in own} == {member_key.id}
    assert denied is None

    async with await _session(
        supabase_engine, tenants.admin_id, tenants.target_workspace_id
    ) as admin:
        all_keys = await mcp_keys.list_keys(
            admin,
            user_id=tenants.admin_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=True,
        )
        assert await mcp_keys.revoke_key(
            admin,
            key_id=viewer_key.id,
            actor_user_id=tenants.admin_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=True,
        )
        await admin.commit()
    assert {item.id for item in all_keys} == {member_key.id, viewer_key.id}

    for explicit_context in (None, tenants.other_workspace_id):
        async with await _session(
            supabase_engine, tenants.other_workspace_member_id, explicit_context
        ) as outsider:
            assert await mcp_keys.list_keys(
                outsider,
                user_id=tenants.other_workspace_member_id,
                workspace_id=tenants.target_workspace_id,
                is_admin=False,
            ) == []
            assert await mcp_keys.revoke_key(
                outsider,
                key_id=member_key.id,
                actor_user_id=tenants.other_workspace_member_id,
                workspace_id=tenants.target_workspace_id,
                is_admin=False,
            ) is None

    async with await _session(supabase_engine, tenants.zero_membership_id) as nonmember:
        with pytest.raises(WorkspaceNotFoundError):
            await api.list_mcp_keys(
                tenants.target_workspace_id, nonmember, _user(tenants.zero_membership_id)
            )


async def test_viewer_can_create_but_departure_blocks_management_not_resolution(
    supabase_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    viewer = _user(tenants.viewer_id)
    async with await _session(supabase_engine, tenants.viewer_id) as session:
        response = Response()
        with patch.object(api, "enforce_rate_limit", new=AsyncMock()):
            created = await api.create_mcp_key(
                tenants.target_workspace_id,
                McpKeyCreate(name="viewer", expires_in_days=30),
                response,
                session,
                viewer,
            )
        await session.commit()
    assert response.headers["Cache-Control"] == "no-store"
    async with await _session(supabase_engine, tenants.viewer_id) as session:
        listed = await api.list_mcp_keys(tenants.target_workspace_id, session, viewer)
        assert [item.id for item in listed.keys] == [created.id]

    async with await _session(
        supabase_engine, tenants.owner_id, tenants.target_workspace_id
    ) as owner:
        await owner.execute(
            text(
                "UPDATE workspace_members SET status = 'departed', departed_at = now() "
                "WHERE workspace_id = CAST(:workspace_id AS uuid) "
                "AND user_id = CAST(:user_id AS uuid)"
            ),
            {
                "workspace_id": str(tenants.target_workspace_id),
                "user_id": str(tenants.viewer_id),
            },
        )
        await owner.commit()

    async with await _session(supabase_engine, tenants.viewer_id) as departed:
        with pytest.raises(WorkspaceNotFoundError):
            await api.list_mcp_keys(tenants.target_workspace_id, departed, viewer)
        await departed.rollback()
        with pytest.raises(WorkspaceNotFoundError), patch.object(
            api, "enforce_rate_limit", new=AsyncMock()
        ):
            await api.create_mcp_key(
                tenants.target_workspace_id,
                McpKeyCreate(name="no longer allowed"),
                Response(),
                departed,
                viewer,
            )
    async with await _session(supabase_engine) as resolver:
        resolved = await mcp_keys.resolve_key(resolver, created.key)
        assert resolved is not None and resolved.user_id == tenants.viewer_id


async def test_events_are_append_only_and_have_scoped_runtime_grants(
    supabase_engine: AsyncEngine,
    owner_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    created = await _create(supabase_engine, tenants, tenants.member_id)
    async with supabase_engine.connect() as connection:
        privileges = (
            await connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, 'mcp_key_events', 'UPDATE'), "
                    "has_table_privilege(current_user, 'mcp_key_events', 'DELETE')"
                )
            )
        ).one()
    assert privileges == (False, False)

    for statement in (
        "UPDATE mcp_key_events SET event = 'revoked' WHERE key_id = CAST(:key_id AS uuid)",
        "DELETE FROM mcp_key_events WHERE key_id = CAST(:key_id AS uuid)",
    ):
        async with owner_engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(text("ALTER TABLE mcp_key_events DISABLE ROW LEVEL SECURITY"))
            with pytest.raises(DBAPIError, match="append-only"):
                await connection.execute(text(statement), {"key_id": str(created.id)})
            await transaction.rollback()


async def test_validation_and_create_rate_limit(
    supabase_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    for payload in (
        {"name": "   "},
        {"name": "ok", "expires_in_days": 7},
        {"name": "x" * 101},
    ):
        with pytest.raises(PydanticValidationError):
            McpKeyCreate.model_validate(payload)

    app = FastAPI()
    app.include_router(api.router, prefix="/v1")

    async def override_db():
        yield None

    async def override_user():
        return _user(tenants.member_id)

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_user] = override_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for payload in (
            {"name": "   "},
            {"name": "ok", "expires_in_days": 7},
            {"name": "x" * 101},
        ):
            response = await client.post(
                f"/v1/workspaces/{tenants.target_workspace_id}/mcp-keys", json=payload
            )
            assert response.status_code == 422

    async with await _session(supabase_engine, tenants.member_id) as member:
        limited = RateLimitExceededError("limited", retry_after_seconds=60)
        with patch.object(api, "enforce_rate_limit", new=AsyncMock(side_effect=limited)):
            with pytest.raises(RateLimitExceededError) as raised:
                await api.create_mcp_key(
                    tenants.target_workspace_id,
                    McpKeyCreate(name="rate limited"),
                    Response(),
                    member,
                    _user(tenants.member_id),
                )
    assert raised.value.http_status == 429


async def test_create_list_and_revoke_never_log_secret_or_hash(
    supabase_engine: AsyncEngine,
    tenants: SecurityTenantData,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.clear()
    created = await _create(supabase_engine, tenants, tenants.member_id)
    digest = mcp_keys.hash_key(created.key)
    async with await _session(
        supabase_engine, tenants.member_id, tenants.target_workspace_id
    ) as member:
        await mcp_keys.list_keys(
            member,
            user_id=tenants.member_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=False,
        )
        await mcp_keys.revoke_key(
            member,
            key_id=created.id,
            actor_user_id=tenants.member_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=False,
        )
        await member.commit()
    assert created.key not in caplog.text
    assert digest not in caplog.text


async def test_touch_is_throttled_and_sensitive_columns_are_not_writable(
    supabase_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    created = await _create(supabase_engine, tenants, tenants.member_id)
    async with await _session(supabase_engine) as runtime:
        await mcp_keys.touch_key(runtime, created.id, created.key)
        await _set_lookup(runtime, created.key)
        first = (
            await runtime.execute(
                text(
                    "SELECT last_used_at FROM mcp_api_keys WHERE id = CAST(:key_id AS uuid)"
                ),
                {"key_id": str(created.id)},
            )
        ).scalar_one()
        await mcp_keys.touch_key(runtime, created.id, created.key)
        await _set_lookup(runtime, created.key)
        second = (
            await runtime.execute(
                text(
                    "SELECT last_used_at FROM mcp_api_keys WHERE id = CAST(:key_id AS uuid)"
                ),
                {"key_id": str(created.id)},
            )
        ).scalar_one()
        assert first == second

    forbidden_updates = (
        (
            "UPDATE mcp_api_keys SET expires_at = expires_at + interval '1 day' "
            "WHERE id = CAST(:key_id AS uuid)",
            {},
        ),
        (
            "UPDATE mcp_api_keys SET user_id = CAST(:value AS uuid) "
            "WHERE id = CAST(:key_id AS uuid)",
            {"value": str(tenants.viewer_id)},
        ),
        (
            "UPDATE mcp_api_keys SET workspace_id = CAST(:value AS uuid) "
            "WHERE id = CAST(:key_id AS uuid)",
            {"value": str(tenants.other_workspace_id)},
        ),
        (
            "UPDATE mcp_api_keys SET key_hash = CAST(:value AS char(64)) "
            "WHERE id = CAST(:key_id AS uuid)",
            {"value": "f" * 64},
        ),
    )
    for statement, extra in forbidden_updates:
        async with await _session(supabase_engine) as runtime:
            await _set_lookup(runtime, created.key)
            with pytest.raises(DBAPIError, match="permission denied"):
                await runtime.execute(
                    text(statement), {"key_id": str(created.id), **extra}
                )

    async with await _session(
        supabase_engine, tenants.member_id, tenants.target_workspace_id
    ) as member:
        await mcp_keys.revoke_key(
            member,
            key_id=created.id,
            actor_user_id=tenants.member_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=False,
        )
        await member.commit()
    async with await _session(supabase_engine) as runtime:
        await _set_lookup(runtime, created.key)
        result = await runtime.execute(
            text(
                "UPDATE mcp_api_keys SET revoked_at = NULL "
                "WHERE id = CAST(:key_id AS uuid)"
            ),
            {"key_id": str(created.id)},
        )
        assert result.rowcount == 0
    async with await _session(
        supabase_engine, tenants.member_id, tenants.target_workspace_id
    ) as member:
        # The revocation trigger fires before the RLS WITH CHECK; either rejection is correct.
        with pytest.raises(DBAPIError, match="row-level security|irreversible"):
            await member.execute(
                text(
                    "UPDATE mcp_api_keys SET revoked_at = NULL "
                    "WHERE id = CAST(:key_id AS uuid)"
                ),
                {"key_id": str(created.id)},
            )


async def test_revoked_key_cannot_be_reactivated_with_member_and_hash_contexts_combined(
    supabase_engine: AsyncEngine,
    tenants: SecurityTenantData,
) -> None:
    """Permissive UPDATE policies are ORed: the member policy (USING) admits a revoked row and the
    hash-lookup policy (WITH CHECK) accepts ``revoked_at IS NULL``. With both contexts set, the
    database layer alone must still keep a revoked key revoked."""
    created = await _create(supabase_engine, tenants, tenants.member_id)
    async with await _session(
        supabase_engine, tenants.admin_id, tenants.target_workspace_id
    ) as admin:
        revoked = await mcp_keys.revoke_key(
            admin,
            key_id=created.id,
            actor_user_id=tenants.admin_id,
            workspace_id=tenants.target_workspace_id,
            is_admin=True,
        )
        await admin.commit()
    assert revoked is not None and revoked.revoked_at is not None

    async def raw_state() -> tuple[object, object]:
        # Forced RLS applies to every role, so read through an admin's own (all-keys) context.
        async with await _session(
            supabase_engine, tenants.admin_id, tenants.target_workspace_id
        ) as admin_read:
            row = (
                await admin_read.execute(
                    text(
                        "SELECT revoked_at, revoked_by FROM mcp_api_keys "
                        "WHERE id = CAST(:key_id AS uuid)"
                    ),
                    {"key_id": str(created.id)},
                )
            ).one()
        return row.revoked_at, row.revoked_by

    before = await raw_state()
    assert before[0] is not None and before[1] == tenants.admin_id

    attempts = (
        "UPDATE mcp_api_keys SET revoked_at = NULL, revoked_by = NULL "
        "WHERE id = CAST(:key_id AS uuid)",
        "UPDATE mcp_api_keys SET revoked_at = NULL WHERE id = CAST(:key_id AS uuid)",
        "UPDATE mcp_api_keys SET revoked_by = CAST(:member AS uuid) "
        "WHERE id = CAST(:key_id AS uuid)",
        "UPDATE mcp_api_keys SET revoked_at = now() + interval '1 year' "
        "WHERE id = CAST(:key_id AS uuid)",
    )
    failures: list[str] = []
    for statement in attempts:
        # The member is still an active member and also presents the matching key hash.
        async with await _session(
            supabase_engine, tenants.member_id, tenants.target_workspace_id
        ) as member:
            await _set_lookup(member, created.key)
            try:
                await member.execute(
                    text(statement),
                    {"key_id": str(created.id), "member": str(tenants.member_id)},
                )
                await member.commit()
            except DBAPIError:
                await member.rollback()
        if await raw_state() != before:
            async with await _session(supabase_engine) as probe:
                live = await mcp_keys.resolve_key(probe, created.key) is not None
            failures.append(f"{statement.split(' WHERE')[0]} -> key usable again: {live}")
            async with await _session(
                supabase_engine, tenants.admin_id, tenants.target_workspace_id
            ) as admin_reset:  # restore so each statement is judged independently
                await admin_reset.execute(
                    text(
                        "UPDATE mcp_api_keys SET revoked_at = now(), "
                        "revoked_by = CAST(:admin AS uuid) WHERE id = CAST(:key_id AS uuid)"
                    ),
                    {"admin": str(tenants.admin_id), "key_id": str(created.id)},
                )
                await admin_reset.commit()
            before = await raw_state()

    async with await _session(supabase_engine) as resolver:
        usable = await mcp_keys.resolve_key(resolver, created.key)
    assert not failures, f"revocation was altered by: {failures}"
    assert usable is None
