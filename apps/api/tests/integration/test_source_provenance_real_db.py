"""Source provenance, authorship and stale-source state against real PostgreSQL.

Runs as the restricted runtime role (NOSUPERUSER, NOBYPASSRLS) under RLS.

Proves:
  * ``fetch_source_context`` reports source, authorship (resolved / unresolved /
    shared) and source state (current / stale, newer source link) for the
    revision a memory was derived from, and nothing for a manual memory;
  * ``get_memory_for_user`` and ``search_memories_for_user`` enforce workspace
    membership on every call: a non-member, a member of another workspace and a
    never-existing id all get the same not-found outcome;
  * a supplied ``workspace_id`` is a selector, never authorization;
  * the REST ``GET /memories/:id`` route and the shared service return the same
    ``source``, ``authorship`` and ``source_state`` (one definition, T-M1).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.api.v1.memories import get_memory
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.core.exceptions import (
    MemoryNotFoundError,
    WorkspaceAccessDeniedError,
    WorkspaceNotFoundError,
)
from sourcemind.models.connector import ArtifactLink
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.services.memory.read import (
    fetch_source_context,
    get_memory_for_user,
    search_memories_for_user,
)
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = pytest.mark.asyncio


async def _session(
    engine: AsyncEngine, user_id: uuid.UUID, workspace_id: uuid.UUID | None
) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(session, user_id)
    if workspace_id is not None:
        await set_rls_workspace_context(session, workspace_id)
    return session


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class Seeded:
    """Ids of the rows the fixture creates inside the target/other workspaces."""

    def __init__(self, data: SecurityTenantData) -> None:
        self.data = data
        self.unresolved_memory = uuid.uuid4()
        self.resolved_memory = uuid.uuid4()
        self.shared_memory = uuid.uuid4()
        self.stale_memory = uuid.uuid4()
        self.manual_memory = data.target_memory_id
        self.other_ws_memory = uuid.uuid4()


async def _link(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    source_id: str,
    url: str,
    seq: int,
    author: str | None,
    resolved_user_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    memory_id: uuid.UUID | None = None,
) -> None:
    session.add(
        ArtifactLink(
            workspace_id=workspace_id,
            document_id=document_id,
            memory_id=memory_id,
            source_tool="github",
            source_type="pull_request",
            source_id=source_id,
            source_url=url,
            source_author=author,
            source_timestamp=datetime(2026, 10, 1, tzinfo=UTC),
            resolved_user_id=resolved_user_id,
            source_revision=f"rev-{seq}",
            revision_seq=seq,
            artifact_metadata=metadata or {},
        )
    )
    await session.flush()


@pytest.fixture
async def seeded(supabase_engine: AsyncEngine) -> Seeded:
    data = await _seed_security_tenants(supabase_engine)
    s = Seeded(data)
    async with AsyncSession(supabase_engine, expire_on_commit=False) as setup:
        await set_rls_user_context(setup, data.owner_id)
        await set_rls_workspace_context(setup, data.target_workspace_id)

        async def doc(label: str) -> uuid.UUID:
            d = Document(
                workspace_id=data.target_workspace_id,
                submitter_id=data.owner_id,
                source_type="text",
                sha256_hash=_hash(label + uuid.uuid4().hex),
                ingestion_status="completed",
            )
            setup.add(d)
            await setup.flush()
            return d.id

        def mem(mid: uuid.UUID, did: uuid.UUID, text_: str, **extra) -> Memory:
            return Memory(
                id=mid,
                workspace_id=data.target_workspace_id,
                document_id=did,
                content=text_,
                content_hash=_hash(text_ + mid.hex),
                **extra,
            )

        d_unres = await doc("unresolved")
        d_res = await doc("resolved")
        d_shared = await doc("shared")
        d_stale = await doc("stale")
        setup.add_all(
            [
                mem(s.unresolved_memory, d_unres, "Unresolved author fact about caching."),
                mem(s.resolved_memory, d_res, "Resolved author fact about caching."),
                mem(s.shared_memory, d_shared, "Shared author fact about caching."),
                mem(
                    s.stale_memory,
                    d_stale,
                    "Edited fact about caching.",
                    source_stale_at=datetime(2026, 10, 5, tzinfo=UTC),
                    source_stale_reason="source_revised",
                ),
            ]
        )
        await setup.flush()
        await _link(
            setup, workspace_id=data.target_workspace_id, document_id=d_unres,
            source_id="o/r/pull/1", url="https://github.test/o/r/pull/1", seq=1,
            author="octocat", memory_id=s.unresolved_memory,
        )
        await _link(
            setup, workspace_id=data.target_workspace_id, document_id=d_res,
            source_id="o/r/pull/2", url="https://github.test/o/r/pull/2", seq=1,
            author="member-login", resolved_user_id=data.member_id,
            memory_id=s.resolved_memory,
        )
        await _link(
            setup, workspace_id=data.target_workspace_id, document_id=d_shared,
            source_id="o/r/commits/abc", url="https://github.test/o/r/commit/abc", seq=1,
            author=None,
            metadata={"authorship": "shared", "source_authors": ["alice", "bob"]},
            memory_id=s.shared_memory,
        )
        # Stale memory: derived from revision 1; revision 2 exists with a new URL.
        await _link(
            setup, workspace_id=data.target_workspace_id, document_id=d_stale,
            source_id="o/r/pull/9", url="https://github.test/o/r/pull/9#old", seq=1,
            author="octocat", memory_id=s.stale_memory,
        )
        d_stale_new = await doc("stale-new")
        await _link(
            setup, workspace_id=data.target_workspace_id, document_id=d_stale_new,
            source_id="o/r/pull/9", url="https://github.test/o/r/pull/9#new", seq=2,
            author="octocat",
        )
        await setup.commit()

    async with AsyncSession(supabase_engine, expire_on_commit=False) as setup:
        await set_rls_user_context(setup, data.owner_id)
        await set_rls_workspace_context(setup, data.other_workspace_id)
        other_doc = Document(
            workspace_id=data.other_workspace_id,
            submitter_id=data.owner_id,
            source_type="text",
            sha256_hash=_hash("other" + uuid.uuid4().hex),
            ingestion_status="completed",
        )
        setup.add(other_doc)
        await setup.flush()
        setup.add(
            Memory(
                id=s.other_ws_memory,
                workspace_id=data.other_workspace_id,
                document_id=other_doc.id,
                content="Other workspace secret about caching.",
                content_hash=_hash("other-ws"),
            )
        )
        await setup.commit()
    return s


async def test_source_context_reports_source_authorship_and_state(
    supabase_engine: AsyncEngine, seeded: Seeded
) -> None:
    data = seeded.data
    async with await _session(supabase_engine, data.member_id, data.target_workspace_id) as db:
        ctx = await fetch_source_context(
            db,
            [
                str(seeded.unresolved_memory),
                str(seeded.resolved_memory),
                str(seeded.shared_memory),
                str(seeded.stale_memory),
                str(seeded.manual_memory),
            ],
        )

    un = ctx[str(seeded.unresolved_memory)]
    assert un.source is not None
    assert (un.source.tool, un.source.type, un.source.id) == ("github", "pull_request", "o/r/pull/1")
    assert un.source.url == "https://github.test/o/r/pull/1"
    assert (un.source.revision, un.source.revision_seq) == ("rev-1", 1)
    assert un.authorship is not None and un.authorship.status == "unresolved"
    assert un.authorship.source_author == "octocat"
    assert un.source_state is not None and un.source_state.status == "current"

    res = ctx[str(seeded.resolved_memory)]
    assert res.authorship is not None and res.authorship.status == "resolved"

    shared = ctx[str(seeded.shared_memory)]
    assert shared.authorship is not None and shared.authorship.status == "shared"
    assert shared.authorship.source_authors == ["alice", "bob"]
    assert shared.authorship.source_author is None  # no single author is invented

    stale = ctx[str(seeded.stale_memory)]
    assert stale.source_state is not None and stale.source_state.status == "stale"
    assert stale.source_state.reason == "source_revised"
    assert stale.source_state.newer_source_url == "https://github.test/o/r/pull/9#new"
    assert stale.source is not None and stale.source.revision_seq == 1  # derived from rev 1

    manual = ctx[str(seeded.manual_memory)]
    assert manual.source is None and manual.authorship is None and manual.source_state is None


async def test_get_memory_for_user_enforces_membership(
    supabase_engine: AsyncEngine, seeded: Seeded
) -> None:
    data = seeded.data
    # A member and a viewer of the target workspace may read.
    for uid in (data.member_id, data.viewer_id):
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            view = await get_memory_for_user(
                db, user_id=uid, memory_id=seeded.unresolved_memory
            )
            assert view.source is not None and view.authorship is not None

    # No membership, another workspace's member, and a random id: all not-found.
    for uid in (data.zero_membership_id, data.other_workspace_member_id):
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            with pytest.raises(MemoryNotFoundError):
                await get_memory_for_user(
                    db, user_id=uid, memory_id=seeded.unresolved_memory
                )
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        with pytest.raises(MemoryNotFoundError):
            await get_memory_for_user(
                db, user_id=data.member_id, memory_id=uuid.uuid4()
            )
    # A target-workspace member cannot read the OTHER workspace's memory either.
    async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
        with pytest.raises(MemoryNotFoundError):
            await get_memory_for_user(
                db, user_id=data.member_id, memory_id=seeded.other_ws_memory
            )


async def test_rest_route_and_service_expose_identical_source_objects(
    supabase_engine: AsyncEngine, seeded: Seeded
) -> None:
    """T-M1: one definition of source / authorship / source_state for REST and MCP."""
    data = seeded.data
    user = AuthenticatedUser(data.member_id, f"member-{data.member_id}", "m@example.com")
    for mid in (
        seeded.unresolved_memory,
        seeded.resolved_memory,
        seeded.shared_memory,
        seeded.stale_memory,
        seeded.manual_memory,
    ):
        async with await _session(supabase_engine, data.member_id, data.target_workspace_id) as db:
            rest = (
                await get_memory(
                    memory_id=mid, db=db, current_user=user,
                    request_id="t-m1", include_attribution=True,
                )
            ).data
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            svc = await get_memory_for_user(db, user_id=data.member_id, memory_id=mid)
        assert rest.source == svc.source
        assert rest.authorship == svc.authorship
        assert rest.source_state == svc.source_state


async def test_search_for_user_denies_non_members_and_ignores_selector(
    supabase_engine: AsyncEngine, seeded: Seeded
) -> None:
    data = seeded.data

    async def fake_embedding(query: str, client: object | None):
        return [0.0] * 3072, True

    patches = (
        patch("sourcemind.services.search.hybrid._get_query_embedding", new=fake_embedding),
        patch("sourcemind.services.memory.read.enforce_rate_limit", new=AsyncMock()),
    )
    with patches[0], patches[1]:
        async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
            ok = await search_memories_for_user(
                db, user_id=data.member_id, workspace_id=data.target_workspace_id,
                query="caching", mode="keyword", limit=10,
            )
        texts = {r.memory.content for r in ok.results}
        assert "Unresolved author fact about caching." in texts
        assert "Other workspace secret about caching." not in texts
        sourced = [r for r in ok.results if r.memory.id == seeded.unresolved_memory]
        assert sourced and sourced[0].memory.source is not None
        assert sourced[0].memory.authorship.status == "unresolved"  # type: ignore[union-attr]

        # The selector names a workspace the user does not belong to: denied.
        for uid, ws in (
            (data.member_id, data.other_workspace_id),
            (data.zero_membership_id, data.target_workspace_id),
            (data.other_workspace_member_id, data.target_workspace_id),
        ):
            async with AsyncSession(supabase_engine, expire_on_commit=False) as db:
                with pytest.raises((WorkspaceNotFoundError, WorkspaceAccessDeniedError)):
                    await search_memories_for_user(
                        db, user_id=uid, workspace_id=ws, query="caching", mode="keyword"
                    )
