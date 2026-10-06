"""github_author_links against disposable PostgreSQL (RLS) and Redis — D-021.

Runs as the restricted runtime role (``sourcemind_test``: NOSUPERUSER,
NOBYPASSRLS), never as the owner, except where the owner is the subject: the
grant test revokes as the owner and re-applies the migration's own grant
function, so the result cannot be an artefact of ALTER DEFAULT PRIVILEGES.

Proves, under real RLS:
  * the runtime ACL on github_author_links is exactly SELECT/INSERT/UPDATE/
    DELETE for the runtime role and nothing for PUBLIC;
  * links never cross workspaces (API path, direct SQL and worker path), and
    non-members and departed members see nothing;
  * GitHub sync credits the admin-linked author, never the sync initiator,
    and finalizes resolved_user_id on the anchor AND its clones;
  * the credit-time recheck: deleted / corrected / departed -> unresolved,
    and FOR SHARE makes a concurrent correction wait;
  * an unresolved author stays reported and counted as missing coverage after
    an edit gives the editor a row on the new version.

Requires SECURITY_TEST_ALLOW_DISPOSABLE=1, TEST_DATABASE_URL/TEST_REDIS_URL
(the disposable tuple validated by tests/conftest.py) and, for the grant test,
MIGRATION_DATABASE_URL naming the disposable owner role.
"""
# The worker-contract fixtures are imported (not copied) so both suites share
# one harness; pytest resolves them by parameter name, which ruff reads as F811.
# ruff: noqa: F811

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.api.v1.github_author_links import (
    GitHubAuthorLinkUpsert,
    delete_github_author_link,
    list_github_author_links,
    list_github_authors,
    put_github_author_link,
)
from sourcemind.api.v1.memories import get_memory
from sourcemind.api.v1.workspaces import revoke_workspace_member
from sourcemind.connectors.github.connector import GitHubConnector
from sourcemind.connectors.github.mapper import GitHubMapper
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.dependencies import WorkspacePermission, require_workspace_permission
from sourcemind.core.exceptions import (
    ValidationError,
    WorkspaceAccessDeniedError,
    WorkspaceNotFoundError,
)
from sourcemind.core.redis_client import init_redis
from sourcemind.services.attribution.github_links import (
    recheck_external_credit,
    resolve_github_link,
)
from sourcemind.services.ingestion.fact_extractor import ExtractionResult as FactExtractionResult
from tests.integration.test_ingestion_worker_contract import (  # noqa: F401 - fixtures
    FakeTask,
    WorkerHarness,
    _user,
    ingestion_worker_database_url,
    ingestion_worker_engine,
    ingestion_worker_environment,
    ingestion_worker_redis_url,
    worker_harness,
)
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

API_ROOT = Path(__file__).resolve().parents[2]
_PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE"}


# ── fixtures and helpers ─────────────────────────────────────────────────────


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


def _runtime_role(engine: AsyncEngine) -> str:
    role = engine.url.username
    assert role == "sourcemind_test", "these tests must run as the restricted runtime role"
    return role


def _load_migration() -> Any:
    path = next((API_ROOT / "alembic" / "versions").glob("*_0010_github_author_links.py"))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _session(
    engine: AsyncEngine, user_id: uuid.UUID, workspace_id: uuid.UUID | None
) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(session, user_id)
    if workspace_id is not None:
        await set_rls_workspace_context(session, workspace_id)
    return session


async def _put_link(
    engine: AsyncEngine,
    data: SecurityTenantData,
    actor: uuid.UUID,
    workspace_id: uuid.UUID,
    github_user_id: int,
    target: uuid.UUID,
):
    session = await _session(engine, actor, None)
    async with session:
        return await put_github_author_link(
            workspace_id=workspace_id,
            github_user_id=github_user_id,
            body=GitHubAuthorLinkUpsert(user_id=target, github_login="label-only"),
            db=session,
            current_user=_user(data, actor),
        )


def _gid() -> int:
    return uuid.uuid4().int % 2_000_000_000 + 1


def _two_facts(harness: WorkerHarness) -> None:
    tag = uuid.uuid4().hex

    async def facts(*_a: object, **_k: object) -> FactExtractionResult:
        return FactExtractionResult(
            facts=[f"Synced fact one {tag}.", f"Synced fact two {tag}."],
            total_chunks=1,
            failed_chunks=0,
            failure_reasons=[],
        )

    harness.extract_facts = facts  # type: ignore[method-assign]


async def _sync_commit(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    data: SecurityTenantData,
    *,
    initiator: uuid.UUID,
    github_user_id: int | None,
    login: str = "alice",
    display_name: str = "Initiator Lookalike",
) -> tuple[uuid.UUID, str]:
    """Run the real connector ingest for one commit; return (document_id, source_id)."""
    from sourcemind.workers import ingestion as worker

    monkeypatch.setattr(
        worker.process_document,
        "apply_async",
        lambda **kwargs: SimpleNamespace(id=kwargs["task_id"]),
    )
    sha = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    raw = {
        "sha": sha,
        "html_url": f"https://github.com/acme/repo/commit/{sha}",
        "commit": {
            "author": {"name": display_name, "date": "2026-10-01T00:00:00Z"},
            "message": f"Synced change {sha}",
        },
        "author": {"login": login, "id": github_user_id} if github_user_id else None,
    }
    doc = GitHubMapper.from_commit("acme", "repo", raw)
    # The worker closes the process-wide Redis client when it finishes.
    await init_redis()
    session = await _session(engine, initiator, None)
    async with session:
        # Same authorization the connector task performs before syncing.
        await require_workspace_permission(
            session, initiator, data.target_workspace_id, WorkspacePermission.ADMINISTER
        )
        connector = GitHubConnector(
            config=SimpleNamespace(id=uuid.uuid4(), config={}, last_sync_at=None),
            auth=SimpleNamespace(),
            session=session,
            workspace_id=data.target_workspace_id,
            user_id=initiator,
        )
        assert await connector._ingest(doc) is True
        await session.commit()

    owner = await _session(engine, data.owner_id, data.target_workspace_id)
    async with owner:
        document_id = await owner.scalar(
            text(
                "SELECT document_id FROM artifact_links "
                "WHERE source_id = :sid AND memory_id IS NULL"
            ),
            {"sid": doc.source_id},
        )
    assert document_id is not None, "the link must be durable before the worker runs"
    return uuid.UUID(str(document_id)), doc.source_id


async def _run_worker(data: SecurityTenantData, document_id: uuid.UUID, initiator: uuid.UUID):
    from sourcemind.workers.ingestion import _run_pipeline

    return await _run_pipeline(
        FakeTask(retries=0), str(document_id), str(data.target_workspace_id), str(initiator)
    )


async def _outcome(
    engine: AsyncEngine, data: SecurityTenantData, document_id: uuid.UUID
) -> dict[str, Any]:
    owner = await _session(engine, data.owner_id, data.target_workspace_id)
    async with owner:
        memory_ids = [
            row.id
            for row in (
                await owner.execute(
                    text("SELECT id FROM memories WHERE document_id = :d ORDER BY created_at"),
                    {"d": document_id},
                )
            ).fetchall()
        ]
        attributed = {
            row.user_id
            for row in (
                await owner.execute(
                    text(
                        "SELECT user_id FROM attributions "
                        "WHERE memory_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": [str(m) for m in memory_ids]},
                )
            ).fetchall()
        }
        editors = {
            row.editor_id
            for row in (
                await owner.execute(
                    text(
                        "SELECT editor_id FROM attribution_edits "
                        "WHERE memory_id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": [str(m) for m in memory_ids]},
                )
            ).fetchall()
        }
        links = (
            await owner.execute(
                text(
                    "SELECT memory_id, resolved_user_id FROM artifact_links "
                    "WHERE document_id = :d"
                ),
                {"d": document_id},
            )
        ).fetchall()
        final = await owner.scalar(
            text("SELECT metadata -> 'attribution' -> 'final' FROM documents WHERE id = :d"),
            {"d": document_id},
        )
    return {
        "memory_ids": memory_ids,
        "attributed": attributed,
        "editors": editors,
        "links": links,
        # Untyped text() SELECT: asyncpg may hand jsonb back as a string.
        "final": json.loads(final) if isinstance(final, str) else final,
    }


# ── 1. grants ────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_runtime_grant_is_explicit_exact_and_not_from_default_privileges(
    ingestion_worker_engine: AsyncEngine,
    owner_engine: AsyncEngine,
) -> None:
    role = _runtime_role(ingestion_worker_engine)

    # Distinct roles: the migration owner owns the table; the runtime role the
    # tests (and the app) use is a different, restricted role.
    async with owner_engine.connect() as conn:
        table_owner = await conn.scalar(
            text(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = 'github_author_links'"
            )
        )
        owner_flags = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert table_owner == owner_engine.url.username == "sourcemind_owner"
    assert table_owner != role
    assert (owner_flags.rolsuper, owner_flags.rolbypassrls) == (False, False)

    async with owner_engine.begin() as conn:
        await conn.execute(text(f'REVOKE ALL ON TABLE github_author_links FROM "{role}"'))

    async with AsyncSession(ingestion_worker_engine) as runtime:
        assert (
            await runtime.scalar(
                text("SELECT has_table_privilege(current_user, 'github_author_links', 'SELECT')")
            )
            is False
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            await runtime.execute(text("SELECT 1 FROM github_author_links"))

    migration = _load_migration()
    async with owner_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: migration.grant_runtime_privileges(
                lambda sql: sync_conn.execute(text(sql)), role, table_owner
            )
        )

    async with AsyncSession(ingestion_worker_engine) as runtime:
        acl = (
            await runtime.execute(
                text(
                    "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' "
                    "            ELSE pg_get_userbyid(a.grantee) END AS grantee, "
                    "       a.privilege_type "
                    "FROM pg_class AS c, aclexplode(c.relacl) AS a "
                    "WHERE c.oid = 'public.github_author_links'::regclass "
                    "  AND a.grantee <> c.relowner"
                )
            )
        ).fetchall()
        flags = (
            await runtime.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'public.github_author_links'::regclass"
                )
            )
        ).one()
        policy = (
            await runtime.execute(
                text(
                    "SELECT policyname, cmd, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'public' AND tablename = 'github_author_links'"
                )
            )
        ).all()
        role_flags = (
            await runtime.execute(
                text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()

    assert {(row.grantee, row.privilege_type) for row in acl} == {
        (role, privilege) for privilege in _PRIVILEGES
    }
    assert not [row for row in acl if row.grantee == "PUBLIC"]
    assert (flags.relrowsecurity, flags.relforcerowsecurity) == (True, True)
    by_cmd = {row.cmd: row for row in policy}
    assert set(by_cmd) == {"SELECT", "INSERT", "UPDATE", "DELETE"}
    for row in policy:
        for expression in filter(None, (row.qual, row.with_check)):
            assert "app.current_user_id" in expression
            assert "departed_at IS NULL" in expression
    admin_role = "'owner'::text, 'admin'::text"
    assert admin_role not in by_cmd["SELECT"].qual
    assert admin_role in by_cmd["INSERT"].with_check
    assert admin_role not in by_cmd["UPDATE"].qual  # FOR SHARE stays member-level
    assert admin_role in by_cmd["UPDATE"].with_check
    assert admin_role in by_cmd["DELETE"].qual
    assert (role_flags.rolsuper, role_flags.rolbypassrls) == (False, False)


# ── 2. RLS on direct SQL ─────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rls_hides_links_from_other_workspaces_non_members_and_departed(
    ingestion_worker_engine: AsyncEngine,
) -> None:
    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    gid = _gid()
    await _put_link(engine, data, data.admin_id, data.target_workspace_id, gid, data.member_id)

    count_sql = text(
        "SELECT count(*) FROM github_author_links WHERE workspace_id = CAST(:ws AS uuid)"
    )
    params = {"ws": str(data.target_workspace_id)}

    outsider = await _session(engine, data.other_workspace_member_id, data.target_workspace_id)
    async with outsider:
        assert await outsider.scalar(count_sql, params) == 0
        updated = await outsider.execute(
            text(
                "UPDATE github_author_links SET user_id = CAST(:u AS uuid) "
                "WHERE workspace_id = CAST(:ws AS uuid)"
            ),
            {"u": str(data.other_workspace_member_id), **params},
        )
        assert updated.rowcount == 0
        deleted = await outsider.execute(
            text("DELETE FROM github_author_links WHERE workspace_id = CAST(:ws AS uuid)"),
            params,
        )
        assert deleted.rowcount == 0
        with pytest.raises(DBAPIError):
            await outsider.execute(
                text(
                    "INSERT INTO github_author_links "
                    "(workspace_id, github_user_id, user_id, created_by_user_id) VALUES "
                    "(CAST(:ws AS uuid), :gid, CAST(:u AS uuid), CAST(:u AS uuid))"
                ),
                {"gid": _gid(), "u": str(data.other_workspace_member_id), **params},
            )

    stranger = await _session(engine, data.zero_membership_id, None)
    async with stranger:
        assert await stranger.scalar(count_sql, params) == 0

    # The owner belongs to both workspaces; the workspace context still scopes.
    owner_in_other = await _session(engine, data.owner_id, data.other_workspace_id)
    async with owner_in_other:
        assert await owner_in_other.scalar(count_sql, params) == 0

    owner = await _session(engine, data.owner_id, None)
    async with owner:
        await revoke_workspace_member(
            workspace_id=data.target_workspace_id,
            user_id=data.viewer_id,
            db=owner,
            current_user=_user(data, data.owner_id),
            idempotency_key=str(uuid.uuid4()),
        )
    departed = await _session(engine, data.viewer_id, data.target_workspace_id)
    async with departed:
        assert await departed.scalar(count_sql, params) == 0

    member = await _session(engine, data.member_id, data.target_workspace_id)
    async with member:
        assert await member.scalar(count_sql, params) == 1


# ── 2b. admin-only writes at the database layer ─────────────────────────────


async def _attempt(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    sql: str,
    params: dict[str, Any],
) -> int | str:
    """Run one statement as the declared user; return rowcount or 'denied'."""
    session = await _session(engine, user_id, workspace_id)
    async with session:
        try:
            result = await session.execute(text(sql), params)
        except DBAPIError as exc:
            assert "row-level security" in str(exc)
            await session.rollback()
            return "denied"
        await session.commit()
        return result.rowcount


@pytest.mark.integration
@pytest.mark.asyncio
async def test_db_layer_allows_writes_only_for_declared_owner_or_admin(
    ingestion_worker_engine: AsyncEngine,
) -> None:
    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    ws = data.target_workspace_id
    gid = _gid()
    await _put_link(engine, data, data.admin_id, ws, gid, data.member_id)

    # An admin of ANOTHER workspace only.
    owner_in_other = await _session(engine, data.owner_id, data.other_workspace_id)
    async with owner_in_other:
        await owner_in_other.execute(
            text(
                "UPDATE workspace_members SET role = 'admin' "
                "WHERE workspace_id = CAST(:ws AS uuid) AND user_id = CAST(:u AS uuid)"
            ),
            {"ws": str(data.other_workspace_id), "u": str(data.other_workspace_member_id)},
        )
        await owner_in_other.commit()

    insert = (
        "INSERT INTO github_author_links "
        "(workspace_id, github_user_id, user_id, created_by_user_id) VALUES "
        "(CAST(:ws AS uuid), :gid, CAST(:u AS uuid), CAST(:actor AS uuid))"
    )
    update = (
        "UPDATE github_author_links SET github_login = :label "
        "WHERE workspace_id = CAST(:ws AS uuid) AND github_user_id = :gid"
    )
    delete = (
        "DELETE FROM github_author_links "
        "WHERE workspace_id = CAST(:ws AS uuid) AND github_user_id = :gid"
    )

    def ins(actor: uuid.UUID) -> dict[str, Any]:
        return {"ws": str(ws), "gid": _gid(), "u": str(data.member_id), "actor": str(actor)}

    for actor in (data.viewer_id, data.member_id):
        assert await _attempt(engine, actor, ws, insert, ins(actor)) == "denied"
        assert await _attempt(
            engine, actor, ws, update, {"ws": str(ws), "gid": gid, "label": "x"}
        ) == "denied"
        assert await _attempt(engine, actor, ws, delete, {"ws": str(ws), "gid": gid}) == 0

    other_admin = data.other_workspace_member_id
    assert await _attempt(engine, other_admin, ws, insert, ins(other_admin)) == "denied"
    assert await _attempt(
        engine, other_admin, ws, update, {"ws": str(ws), "gid": gid, "label": "x"}
    ) == 0
    assert await _attempt(engine, other_admin, ws, delete, {"ws": str(ws), "gid": gid}) == 0

    # The row survived every refused write, and a member can still lock it.
    member = await _session(engine, data.member_id, ws)
    async with member:
        locked = (
            await member.execute(
                text(
                    "SELECT user_id, github_login FROM github_author_links "
                    "WHERE workspace_id = CAST(:ws AS uuid) AND github_user_id = :gid "
                    "FOR SHARE"
                ),
                {"ws": str(ws), "gid": gid},
            )
        ).one()
        await member.rollback()
    assert locked.user_id == data.member_id
    assert locked.github_login == "label-only"

    # Declared owner and admin of THIS workspace may write.
    assert await _attempt(
        engine, data.owner_id, ws, update, {"ws": str(ws), "gid": gid, "label": "owner"}
    ) == 1
    admin_insert = ins(data.admin_id)
    assert await _attempt(engine, data.admin_id, ws, insert, admin_insert) == 1
    assert await _attempt(
        engine, data.admin_id, ws, delete, {"ws": str(ws), "gid": admin_insert["gid"]}
    ) == 1
    assert await _attempt(engine, data.owner_id, ws, delete, {"ws": str(ws), "gid": gid}) == 1


# ── 3. resolution never crosses workspaces ──────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_link_never_resolves_for_a_document_in_another_workspace(
    ingestion_worker_engine: AsyncEngine,
) -> None:
    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    gid = _gid()
    await _put_link(engine, data, data.admin_id, data.target_workspace_id, gid, data.member_id)

    owner_in_other = await _session(engine, data.owner_id, data.other_workspace_id)
    async with owner_in_other:
        lookup = await resolve_github_link(owner_in_other, data.other_workspace_id, gid)
        decision = await recheck_external_credit(
            owner_in_other,
            data.other_workspace_id,
            {"mode": "external", "github_user_id": gid, "link_user_id": str(data.member_id)},
        )
        # A link row in the other workspace that names a non-member of it.
        await owner_in_other.execute(
            text(
                "INSERT INTO github_author_links "
                "(workspace_id, github_user_id, user_id, created_by_user_id) VALUES "
                "(CAST(:ws AS uuid), :gid, CAST(:u AS uuid), CAST(:actor AS uuid))"
            ),
            {
                "ws": str(data.other_workspace_id),
                "gid": gid,
                "u": str(data.member_id),
                "actor": str(data.owner_id),
            },
        )
        non_member = await resolve_github_link(owner_in_other, data.other_workspace_id, gid)
        await owner_in_other.rollback()

    assert (lookup.user_id, lookup.status) == (None, "unlinked")
    assert (decision.status, decision.reason) == ("unresolved", "link_removed")
    assert (non_member.user_id, non_member.status) == (None, "unlinked")


# ── 4. admin API path ────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_api_manages_links_under_rls_without_crossing_workspaces(
    ingestion_worker_engine: AsyncEngine,
) -> None:
    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    gid = _gid()

    created = await _put_link(
        engine, data, data.admin_id, data.target_workspace_id, gid, data.member_id
    )
    assert created.created_by_user_id == data.admin_id
    assert created.updated_by_user_id is None

    # Same numeric id, other workspace, different person: an independent link.
    await _put_link(
        engine, data, data.owner_id, data.other_workspace_id, gid, data.other_workspace_member_id
    )

    corrected = await _put_link(
        engine, data, data.admin_id, data.target_workspace_id, gid, data.viewer_id
    )
    assert corrected.id == created.id
    assert corrected.user_id == data.viewer_id
    assert corrected.created_by_user_id == data.admin_id
    assert corrected.updated_by_user_id == data.admin_id
    assert corrected.updated_at is not None

    async def listed(actor: uuid.UUID, workspace_id: uuid.UUID):
        session = await _session(engine, actor, None)
        async with session:
            return await list_github_author_links(
                workspace_id=workspace_id, db=session, current_user=_user(data, actor)
            )

    target_links = await listed(data.admin_id, data.target_workspace_id)
    other_links = await listed(data.owner_id, data.other_workspace_id)
    assert [(i.github_user_id, i.user_id) for i in target_links.items] == [(gid, data.viewer_id)]
    assert [(i.github_user_id, i.user_id) for i in other_links.items] == [
        (gid, data.other_workspace_member_id)
    ]

    with pytest.raises(WorkspaceAccessDeniedError):
        await listed(data.member_id, data.target_workspace_id)
    with pytest.raises(WorkspaceNotFoundError):
        await listed(data.other_workspace_member_id, data.target_workspace_id)
    with pytest.raises(WorkspaceNotFoundError):
        await _put_link(
            engine, data, data.admin_id, data.other_workspace_id, _gid(), data.admin_id
        )
    with pytest.raises(ValidationError):
        await _put_link(
            engine,
            data,
            data.admin_id,
            data.target_workspace_id,
            _gid(),
            data.other_workspace_member_id,
        )

    session = await _session(engine, data.admin_id, None)
    async with session:
        first = await delete_github_author_link(
            workspace_id=data.target_workspace_id,
            github_user_id=gid,
            db=session,
            current_user=_user(data, data.admin_id),
        )
    session = await _session(engine, data.admin_id, None)
    async with session:
        second = await delete_github_author_link(
            workspace_id=data.target_workspace_id,
            github_user_id=gid,
            db=session,
            current_user=_user(data, data.admin_id),
        )
    assert (first, second) == ({"deleted": True}, {"deleted": False})
    assert (await listed(data.admin_id, data.target_workspace_id)).items == []
    assert len((await listed(data.owner_id, data.other_workspace_id)).items) == 1


# ── 5. worker path: credited ─────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_credits_linked_author_not_initiator_and_finalizes_all_links(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    _two_facts(worker_harness)
    gid = _gid()
    await _put_link(engine, data, data.admin_id, data.target_workspace_id, gid, data.member_id)

    document_id, _sid = await _sync_commit(
        engine, monkeypatch, data, initiator=data.admin_id, github_user_id=gid
    )
    before = await _outcome(engine, data, document_id)
    assert [row.resolved_user_id for row in before["links"]] == [None]

    result = await _run_worker(data, document_id, data.admin_id)
    after = await _outcome(engine, data, document_id)

    assert result["status"] == "completed"
    assert len(after["memory_ids"]) == 2
    assert after["attributed"] == {data.member_id}
    assert after["editors"] == {data.member_id}
    assert data.admin_id not in after["attributed"] | after["editors"]
    assert len(after["links"]) == 2
    assert {row.memory_id for row in after["links"]} == set(after["memory_ids"])
    assert {row.resolved_user_id for row in after["links"]} == {data.member_id}
    assert after["final"] == {"status": "credited", "user_id": str(data.member_id)}

    rerun = await _run_worker(data, document_id, data.admin_id)
    assert rerun["already_completed"] is True
    assert await _outcome(engine, data, document_id) == after

    session = await _session(engine, data.admin_id, None)
    async with session:
        authors = await list_github_authors(
            workspace_id=data.target_workspace_id,
            db=session,
            current_user=_user(data, data.admin_id),
        )
    assert [(a.github_user_id, a.linked_user_id) for a in authors.items] == [
        (gid, data.member_id)
    ]


# ── 6. credit-time recheck on the worker path ───────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["deleted", "corrected", "departed"])
async def test_link_change_after_queueing_leaves_author_unresolved(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
    change: str,
) -> None:
    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    _two_facts(worker_harness)
    gid = _gid()
    await _put_link(engine, data, data.admin_id, data.target_workspace_id, gid, data.member_id)
    document_id, _sid = await _sync_commit(
        engine, monkeypatch, data, initiator=data.admin_id, github_user_id=gid
    )

    if change == "deleted":
        session = await _session(engine, data.admin_id, None)
        async with session:
            await delete_github_author_link(
                workspace_id=data.target_workspace_id,
                github_user_id=gid,
                db=session,
                current_user=_user(data, data.admin_id),
            )
        reason = "link_removed"
    elif change == "corrected":
        await _put_link(
            engine, data, data.admin_id, data.target_workspace_id, gid, data.viewer_id
        )
        reason = "link_changed"
    else:
        session = await _session(engine, data.owner_id, None)
        async with session:
            await revoke_workspace_member(
                workspace_id=data.target_workspace_id,
                user_id=data.member_id,
                db=session,
                current_user=_user(data, data.owner_id),
                idempotency_key=str(uuid.uuid4()),
            )
        reason = "member_inactive"

    result = await _run_worker(data, document_id, data.admin_id)
    outcome = await _outcome(engine, data, document_id)

    assert result["status"] == "completed"
    assert len(outcome["memory_ids"]) == 2
    assert outcome["attributed"] == set()
    assert outcome["editors"] == set()
    assert len(outcome["links"]) == 2
    assert {row.resolved_user_id for row in outcome["links"]} == {None}
    assert outcome["final"] == {"status": "unresolved", "reason": reason}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_correction_waits_for_the_credit_time_share_lock(
    ingestion_worker_engine: AsyncEngine,
) -> None:
    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    gid = _gid()
    await _put_link(engine, data, data.admin_id, data.target_workspace_id, gid, data.member_id)
    correction = text(
        "UPDATE github_author_links SET user_id = CAST(:u AS uuid) "
        "WHERE workspace_id = CAST(:ws AS uuid) AND github_user_id = :gid"
    )
    params = {"u": str(data.viewer_id), "ws": str(data.target_workspace_id), "gid": gid}

    worker = await _session(engine, data.admin_id, data.target_workspace_id)
    async with worker:
        decision = await recheck_external_credit(
            worker,
            data.target_workspace_id,
            {"mode": "external", "github_user_id": gid, "link_user_id": str(data.member_id)},
        )
        assert decision.status == "credited"

        admin = await _session(engine, data.admin_id, data.target_workspace_id)
        async with admin:
            await admin.execute(text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                await admin.execute(correction, params)
            await admin.rollback()

        await worker.commit()

    admin = await _session(engine, data.admin_id, data.target_workspace_id)
    async with admin:
        assert (await admin.execute(correction, params)).rowcount == 1
        await admin.commit()


# ── 7. unresolved author survives an edit ───────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unresolved_author_stays_reported_and_missing_after_an_edit(
    ingestion_worker_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    worker_harness: WorkerHarness,
) -> None:
    from sourcemind.services.analytics.workspace import get_overview
    from sourcemind.services.attribution import engine as attribution_engine
    from sourcemind.services.attribution.engine import recompute_attribution
    from sourcemind.services.attribution.versioning import create_new_version
    from sourcemind.services.search.hybrid import _fetch_unresolved_authors

    engine = ingestion_worker_engine
    data = await _seed_security_tenants(engine)
    _two_facts(worker_harness)
    # An unlinked numeric id whose login and git display name are decoys equal
    # to a member's identifiers: nothing may resolve on them.
    member_handle = data.clerk_ids[data.member_id]
    document_id, _sid = await _sync_commit(
        engine,
        monkeypatch,
        data,
        initiator=data.admin_id,
        github_user_id=_gid(),
        login=member_handle,
        display_name=f"{member_handle}@example.com",
    )
    await _run_worker(data, document_id, data.admin_id)
    synced = await _outcome(engine, data, document_id)
    assert synced["attributed"] == set()
    assert synced["final"] == {"status": "unresolved", "reason": "unlinked_at_sync"}

    # The 5-signal scorer is never loaded here (spaCy cannot load on 3.14);
    # with no history on the new version the editor is the sole contributor,
    # which is the pre-existing versioning behaviour being exercised.
    monkeypatch.setattr(
        attribution_engine,
        "get_scorer",
        lambda: SimpleNamespace(
            compute_scores=lambda edits: [
                SimpleNamespace(
                    user_id=edits[-1].user_id,
                    contribution_weight=1.0,
                    char_diff_score=1.0,
                    semantic_score=1.0,
                    temporal_score=1.0,
                    structural_score=0.0,
                    approval_score=0.0,
                )
            ]
        ),
    )
    edited_id = synced["memory_ids"][0]
    member = await _session(engine, data.member_id, data.target_workspace_id)
    async with member:
        version = await create_new_version(
            session=member,
            memory_id=edited_id,
            new_content="Edited synced fact.",
            new_tags=None,
            openai_client=None,
        )
        await recompute_attribution(
            session=member,
            memory_id=version.new_memory.id,
            editor_id=data.member_id,
            content_before="Synced fact.",
            content_after="Edited synced fact.",
        )
        await member.commit()
        new_id = version.new_memory.id

    await init_redis()  # the worker closed it; analytics caches in Redis
    reader = await _session(engine, data.member_id, data.target_workspace_id)
    async with reader:
        unresolved = await _fetch_unresolved_authors(reader, [str(new_id)])
        detail = await get_memory(
            memory_id=new_id,
            db=reader,
            current_user=_user(data, data.member_id),
            request_id="github-attribution",
            include_attribution=True,
        )
        overview = await get_overview(reader, data.target_workspace_id)

    assert unresolved[str(new_id)] == {
        "status": "unresolved",
        "source_author": member_handle,
        "source_tool": "github",
    }
    assert detail.data.unresolved_author is not None
    assert [c.user.id for c in detail.data.attribution or []] == [data.member_id]
    assert data.admin_id not in {c.user.id for c in detail.data.attribution or []}
    # Current memories: the seeded memory (no rows), the untouched synced
    # memory (no rows) and the edited version (editor row, unresolved author).
    assert overview["total_memories"] == 3
    assert overview["unattributed_memories"] == 3
    assert overview["health_breakdown"]["coverage"] == 0.0
