"""Actual-API regression coverage for atomic handoff assignment."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.core.database import (
    get_db_session,
    set_rls_user_context,
    set_rls_workspace_context,
)
from sourcemind.core.dependencies import AuthenticatedUser, get_current_user
from sourcemind.models.attribution import Attribution
from sourcemind.models.memory import Memory
from sourcemind.models.organization import Organization
from sourcemind.models.user import User
from sourcemind.models.workspace import Workspace, WorkspaceMember


@dataclass(frozen=True)
class HandoffData:
    workspace_id: uuid.UUID
    admin_id: uuid.UUID
    departing_user_id: uuid.UUID
    recipient_id: uuid.UUID
    handoff_id: uuid.UUID
    included_memory_id: uuid.UUID
    unrelated_memory_id: uuid.UUID


@dataclass(frozen=True)
class AssignmentState:
    assigned_count: int
    assigned_owner_id: str | None
    assignment_note: str | None
    included_recipient_attributions: int
    unrelated_recipient_attributions: int
    unrelated_assignment_rows: int


@pytest.fixture(scope="session")
def handoff_assignment_database_url(request: pytest.FixtureRequest) -> str:
    raw_url = os.getenv("HANDOFF_ASSIGNMENT_DATABASE_URL", "")
    if not raw_url:
        return str(request.getfixturevalue("supabase_url"))

    if os.getenv("HANDOFF_ASSIGNMENT_TEST_ALLOW_DISPOSABLE") != "1":
        raise RuntimeError("custom handoff assignment database target is not enabled")
    expected_database = os.getenv("HANDOFF_ASSIGNMENT_EXPECTED_DATABASE", "")
    expected_port = os.getenv("HANDOFF_ASSIGNMENT_EXPECTED_PORT", "")
    if not raw_url or not expected_database or not expected_port:
        raise RuntimeError("handoff assignment database identity is incomplete")
    if not raw_url.startswith("postgresql+asyncpg://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = make_url(raw_url)
    if parsed.host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("refusing non-loopback handoff assignment database")
    if (
        parsed.username != "sourcemind_test"
        or parsed.database != expected_database
        or parsed.port != int(expected_port)
    ):
        raise RuntimeError("refusing unexpected handoff assignment database identity")
    return raw_url


@pytest_asyncio.fixture
async def handoff_assignment_engine(
    handoff_assignment_database_url: str,
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        handoff_assignment_database_url,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_handoff(engine: AsyncEngine) -> HandoffData:
    suffix = uuid.uuid4().hex
    owner_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    departing_user_id = uuid.uuid4()
    recipient_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    handoff_id = uuid.uuid4()

    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add_all(
            [
                User(
                    id=user_id,
                    clerk_id=f"handoff-{label}-{suffix}",
                    email=f"handoff-{label}-{suffix}@example.invalid",
                    display_name=label.title(),
                )
                for label, user_id in (
                    ("owner", owner_id),
                    ("admin", admin_id),
                    ("departing", departing_user_id),
                    ("recipient", recipient_id),
                )
            ]
        )
        organization = Organization(
            name="Handoff Assignment Regression",
            slug=f"handoff-assignment-{suffix}",
        )
        session.add(organization)
        await session.flush()

        await set_rls_user_context(session, owner_id)
        await set_rls_workspace_context(session, workspace_id)
        session.add(
            Workspace(
                id=workspace_id,
                organization_id=organization.id,
                created_by_user_id=owner_id,
                name="Handoff Assignment Workspace",
                slug=f"handoff-assignment-{suffix}",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                user_id=owner_id,
                role="owner",
            )
        )
        await session.flush()
        session.add_all(
            [
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=admin_id,
                    role="admin",
                ),
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=departing_user_id,
                    role="member",
                    status="departing",
                ),
                WorkspaceMember(
                    workspace_id=workspace_id,
                    user_id=recipient_id,
                    role="member",
                ),
            ]
        )
        await session.flush()

        included_memory = Memory(
            workspace_id=workspace_id,
            content="Memory included in the handoff.",
            content_hash=hashlib.sha256(f"included-{suffix}".encode()).hexdigest(),
        )
        unrelated_memory = Memory(
            workspace_id=workspace_id,
            content="Valid workspace memory outside this handoff.",
            content_hash=hashlib.sha256(f"unrelated-{suffix}".encode()).hexdigest(),
        )
        session.add_all([included_memory, unrelated_memory])
        await session.flush()
        session.add_all(
            [
                Attribution(
                    memory_id=memory.id,
                    user_id=departing_user_id,
                    contribution_weight=0.8,
                    trigger_action="create",
                )
                for memory in (included_memory, unrelated_memory)
            ]
        )
        await session.execute(
            text(
                "INSERT INTO handoff_records "
                "(id, workspace_id, departing_user_id, initiated_by, "
                "tier_1_count, expires_at) "
                "VALUES (CAST(:id AS uuid), CAST(:workspace AS uuid), "
                "CAST(:departing AS uuid), CAST(:initiated_by AS uuid), 1, :expires_at)"
            ),
            {
                "id": str(handoff_id),
                "workspace": str(workspace_id),
                "departing": str(departing_user_id),
                "initiated_by": str(admin_id),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
            },
        )
        await session.execute(
            text(
                "INSERT INTO handoff_assignments (handoff_id, memory_id, tier) "
                "VALUES (CAST(:handoff AS uuid), CAST(:memory AS uuid), 1)"
            ),
            {"handoff": str(handoff_id), "memory": str(included_memory.id)},
        )
        await session.commit()

    return HandoffData(
        workspace_id=workspace_id,
        admin_id=admin_id,
        departing_user_id=departing_user_id,
        recipient_id=recipient_id,
        handoff_id=handoff_id,
        included_memory_id=included_memory.id,
        unrelated_memory_id=unrelated_memory.id,
    )


async def _read_state(engine: AsyncEngine, data: HandoffData) -> AssignmentState:
    async with AsyncSession(engine) as session:
        await set_rls_user_context(session, data.admin_id)
        await set_rls_workspace_context(session, data.workspace_id)
        row = (
            await session.execute(
                text(
                    "SELECT record.assigned_count, assignment.new_owner_id::text, "
                    "assignment.note, "
                    "(SELECT count(*) FROM attributions included_attribution "
                    " WHERE included_attribution.memory_id = CAST(:included AS uuid) "
                    " AND included_attribution.user_id = CAST(:recipient AS uuid)), "
                    "(SELECT count(*) FROM attributions unrelated_attribution "
                    " WHERE unrelated_attribution.memory_id = CAST(:unrelated AS uuid) "
                    " AND unrelated_attribution.user_id = CAST(:recipient AS uuid)), "
                    "(SELECT count(*) FROM handoff_assignments unrelated_assignment "
                    " WHERE unrelated_assignment.handoff_id = record.id "
                    " AND unrelated_assignment.memory_id = CAST(:unrelated AS uuid)) "
                    "FROM handoff_records record "
                    "JOIN handoff_assignments assignment "
                    "ON assignment.handoff_id = record.id "
                    "AND assignment.memory_id = CAST(:included AS uuid) "
                    "WHERE record.id = CAST(:handoff AS uuid)"
                ),
                {
                    "handoff": str(data.handoff_id),
                    "included": str(data.included_memory_id),
                    "unrelated": str(data.unrelated_memory_id),
                    "recipient": str(data.recipient_id),
                },
            )
        ).one()
    return AssignmentState(*row)


async def _post_assignment(
    client: httpx.AsyncClient,
    data: HandoffData,
    memory_id: uuid.UUID,
    note: str,
) -> httpx.Response:
    return await client.post(
        f"/v1/workspaces/{data.workspace_id}/handoff/assign",
        json={
            "handoff_record_id": str(data.handoff_id),
            "memory_id": str(memory_id),
            "new_owner_id": str(data.recipient_id),
            "note": note,
        },
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handoff_assignment_is_atomic_and_limited_to_seeded_memories(
    handoff_assignment_engine: AsyncEngine,
) -> None:
    data = await _seed_handoff(handoff_assignment_engine)
    async with AsyncSession(handoff_assignment_engine) as identity_session:
        identity = (
            await identity_session.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
    assert identity == ("sourcemind_test", False, False)

    from sourcemind.main import create_app

    app = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(handoff_assignment_engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, data.admin_id)
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_current_user() -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=data.admin_id,
            clerk_id="handoff-assignment-admin",
            email="handoff-assignment-admin@example.invalid",
        )

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user
    baseline = await _read_state(handoff_assignment_engine, data)
    assert baseline == AssignmentState(0, None, None, 0, 0, 0)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://handoff.test") as client:
            unrelated = await _post_assignment(
                client,
                data,
                data.unrelated_memory_id,
                "must not be persisted",
            )
            after_unrelated = await _read_state(handoff_assignment_engine, data)
            assert (unrelated.status_code, after_unrelated) == (404, baseline), (
                "an unrelated memory changed handoff attribution or progress: "
                f"status={unrelated.status_code}, state={after_unrelated}"
            )

            legitimate = await _post_assignment(
                client,
                data,
                data.included_memory_id,
                "approved successor",
            )
            assert legitimate.status_code == 200, legitimate.text
            assert legitimate.json()["memory_id"] == str(data.included_memory_id)
            after_legitimate = await _read_state(handoff_assignment_engine, data)
            assert after_legitimate == AssignmentState(
                assigned_count=1,
                assigned_owner_id=str(data.recipient_id),
                assignment_note="approved successor",
                included_recipient_attributions=1,
                unrelated_recipient_attributions=0,
                unrelated_assignment_rows=0,
            )

            repeated = await _post_assignment(
                client,
                data,
                data.included_memory_id,
                "retry must not append",
            )
            after_repeated = await _read_state(handoff_assignment_engine, data)
            assert (repeated.status_code, after_repeated) == (404, after_legitimate)
    finally:
        app.dependency_overrides.clear()
