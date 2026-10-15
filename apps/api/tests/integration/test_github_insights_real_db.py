"""GitHub contributor insights against PostgreSQL under the restricted runtime role."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.api.v1 import connectors as api
from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.core.dependencies import AuthenticatedUser
from sourcemind.core.exceptions import ValidationError, WorkspaceNotFoundError
from sourcemind.models.connector import (
    ArtifactHead,
    ArtifactLink,
    ConnectorConfig,
    ConnectorSyncCursor,
    GitHubAuthorLink,
)
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.services.connectors.insights import github_insights
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

WINDOW_START = datetime(2026, 9, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 10, 1, tzinfo=UTC)
ACME_REPO = "acme/repo"


def _user(user_id: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(user_id, f"clerk-{user_id}", f"{user_id}@example.com")


async def _session(
    engine: AsyncEngine,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID | None = None,
) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(session, user_id)
    if workspace_id is not None:
        await set_rls_workspace_context(session, workspace_id)
    return session


def _hash(label: str) -> str:
    return hashlib.sha256(f"{label}-{uuid.uuid4()}".encode()).hexdigest()


def _account(github_user_id: int, login: str) -> dict:
    return {
        "authorship": "single",
        "author": {
            "kind": "github_account",
            "github_user_id": github_user_id,
            "login": login,
        },
    }


def _git_name(label: str) -> dict:
    return {
        "authorship": "single",
        "author": {"kind": "git_name", "github_user_id": None, "login": None},
        "git_label": label,
    }


@dataclass
class InsightsWorld:
    data: SecurityTenantData
    connector_id: uuid.UUID
    disconnected_id: uuid.UUID
    other_connector_id: uuid.UUID


async def _add_artifact(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    submitter_id: uuid.UUID,
    connector_id: uuid.UUID,
    repo: str,
    source_type: str,
    artifact_id: str,
    timestamp: datetime,
    metadata: dict,
    source_author: str | None,
    revisions: tuple[int, ...] = (1,),
    published_seq: int = 1,
    with_clone: bool = False,
) -> None:
    source_id = f"{repo}/{source_type}s/{artifact_id}"
    documents: dict[int, Document] = {}
    for revision in revisions:
        document = Document(
            workspace_id=workspace_id,
            submitter_id=submitter_id,
            source_type="github",
            sha256_hash=_hash(f"{source_id}-{revision}"),
            ingestion_status="completed",
            connector_id=connector_id,
            connector_generation=0,
        )
        session.add(document)
        await session.flush()
        documents[revision] = document
        session.add(
            ArtifactLink(
                workspace_id=workspace_id,
                document_id=document.id,
                source_tool="github",
                source_type=source_type,
                source_id=source_id,
                source_author=source_author,
                source_timestamp=timestamp + timedelta(minutes=revision),
                artifact_metadata=metadata,
                source_revision=_hash(f"revision-{revision}"),
                revision_seq=revision,
                connector_id=connector_id,
                connector_generation=0,
            )
        )
        if with_clone and revision == published_seq:
            memory = Memory(
                workspace_id=workspace_id,
                document_id=document.id,
                content=f"memory for {source_id}",
                content_hash=_hash(f"memory-{source_id}"),
            )
            session.add(memory)
            await session.flush()
            session.add(
                ArtifactLink(
                    workspace_id=workspace_id,
                    document_id=document.id,
                    memory_id=memory.id,
                    source_tool="github",
                    source_type=source_type,
                    source_id=source_id,
                    source_author=source_author,
                    source_timestamp=timestamp + timedelta(minutes=revision),
                    artifact_metadata=metadata,
                    source_revision=_hash(f"clone-{revision}"),
                    revision_seq=revision,
                    connector_id=connector_id,
                    connector_generation=0,
                )
            )
    session.add(
        ArtifactHead(
            workspace_id=workspace_id,
            source_tool="github",
            source_type=source_type,
            source_id=source_id,
            latest_seq=max(revisions),
            published_seq=published_seq,
            published_document_id=documents[published_seq].id,
        )
    )


@pytest.fixture
async def insights_world(supabase_engine: AsyncEngine) -> InsightsWorld:
    data = await _seed_security_tenants(supabase_engine)
    world = InsightsWorld(data, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    ws = data.target_workspace_id
    async with await _session(supabase_engine, data.owner_id, ws) as session:
        await session.execute(
            text("UPDATE users SET display_name = 'Linked Owner' WHERE id = CAST(:id AS uuid)"),
            {"id": str(data.owner_id)},
        )
        session.add_all(
            [
                ConnectorConfig(
                    id=world.connector_id,
                    workspace_id=ws,
                    connector_type="github",
                    display_name=f"insights-active-{uuid.uuid4().hex[:8]}",
                    config={"repos": [ACME_REPO, "other/repo"]},
                    status="active",
                    created_by=data.owner_id,
                ),
                ConnectorConfig(
                    id=world.disconnected_id,
                    workspace_id=ws,
                    connector_type="github",
                    display_name=f"insights-disconnected-{uuid.uuid4().hex[:8]}",
                    config={"repos": ["acme/old"]},
                    status="disconnected",
                    created_by=data.owner_id,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                ConnectorSyncCursor(
                    connector_id=world.connector_id,
                    workspace_id=ws,
                    repo=ACME_REPO,
                    artifact_type="commits",
                    listing_complete=True,
                ),
                ConnectorSyncCursor(
                    connector_id=world.connector_id,
                    workspace_id=ws,
                    repo=ACME_REPO,
                    artifact_type="issues",
                    listing_complete=False,
                ),
                ConnectorSyncCursor(
                    connector_id=world.connector_id,
                    workspace_id=ws,
                    repo="other/repo",
                    artifact_type="commits",
                    listing_complete=True,
                ),
                ConnectorSyncCursor(
                    connector_id=world.disconnected_id,
                    workspace_id=ws,
                    repo="acme/old",
                    artifact_type="commits",
                    listing_complete=False,
                ),
                GitHubAuthorLink(
                    workspace_id=ws,
                    github_user_id=101,
                    github_login="alice",
                    user_id=data.owner_id,
                    created_by_user_id=data.owner_id,
                ),
            ]
        )

        common = {
            "session": session,
            "workspace_id": ws,
            "submitter_id": data.owner_id,
            "connector_id": world.connector_id,
            "repo": ACME_REPO,
        }
        await _add_artifact(
            **common,
            source_type="commit",
            artifact_id="alice-commit",
            timestamp=WINDOW_START + timedelta(days=1),
            metadata=_account(101, "alice-old"),
            source_author="alice-old",
            with_clone=True,
        )
        await _add_artifact(
            **common,
            source_type="pull_request",
            artifact_id="11",
            timestamp=WINDOW_START + timedelta(days=2),
            metadata=_account(101, "alice"),
            source_author="alice",
        )
        await _add_artifact(
            **common,
            source_type="pull_review",
            artifact_id="12",
            timestamp=WINDOW_START + timedelta(days=3),
            metadata=_account(101, "alice"),
            source_author="alice",
        )
        await _add_artifact(
            **common,
            source_type="pull_review_comment",
            artifact_id="13",
            timestamp=WINDOW_START + timedelta(days=4),
            metadata=_account(101, "alice"),
            source_author="alice",
        )
        await _add_artifact(
            **common,
            source_type="issue",
            artifact_id="14",
            timestamp=WINDOW_START + timedelta(days=5),
            metadata=_account(101, "alice"),
            source_author="alice",
            revisions=(1, 2, 3),
            published_seq=2,
        )
        await _add_artifact(
            **common,
            source_type="issue_comment",
            artifact_id="15",
            timestamp=WINDOW_START + timedelta(days=6),
            metadata=_account(101, "alice"),
            source_author="alice",
        )
        tied_at = WINDOW_START + timedelta(days=7)
        await _add_artifact(
            **common,
            source_type="commit",
            artifact_id="bob-commit",
            timestamp=tied_at,
            metadata=_account(202, "bob"),
            source_author="bob",
        )
        await _add_artifact(
            **common,
            source_type="commit",
            artifact_id="ghost-commit",
            timestamp=tied_at,
            metadata=_git_name("Ghost"),
            source_author="Ghost",
        )
        await _add_artifact(
            **common,
            source_type="commit",
            artifact_id="shared-commit",
            timestamp=WINDOW_START + timedelta(days=8),
            metadata={
                "authorship": "shared",
                "source_authors": ["Alice Shared", "Co Author"],
            },
            source_author=None,
        )
        await _add_artifact(
            **{**common, "repo": "other/repo"},
            source_type="discussion",
            artifact_id="22",
            timestamp=WINDOW_START + timedelta(days=9),
            metadata=_account(101, "alice-latest"),
            source_author="alice-latest",
        )
        await _add_artifact(
            **common,
            source_type="issue",
            artifact_id="outside-window",
            timestamp=WINDOW_START - timedelta(days=5),
            metadata=_account(101, "alice"),
            source_author="alice",
        )
        await _add_artifact(
            session=session,
            workspace_id=ws,
            submitter_id=data.owner_id,
            connector_id=world.disconnected_id,
            repo="acme/old",
            source_type="commit",
            artifact_id="disconnected",
            timestamp=WINDOW_START + timedelta(days=10),
            metadata=_account(303, "disconnected-user"),
            source_author="disconnected-user",
        )
        await session.commit()

    other_ws = data.other_workspace_id
    other_user = data.owner_id
    async with await _session(supabase_engine, other_user, other_ws) as session:
        session.add(
            ConnectorConfig(
                id=world.other_connector_id,
                workspace_id=other_ws,
                connector_type="github",
                display_name=f"insights-other-{uuid.uuid4().hex[:8]}",
                config={"repos": ["evil/repo"]},
                status="active",
                created_by=other_user,
            )
        )
        await session.flush()
        await _add_artifact(
            session=session,
            workspace_id=other_ws,
            submitter_id=other_user,
            connector_id=world.other_connector_id,
            repo="evil/repo",
            source_type="commit",
            artifact_id="secret",
            timestamp=WINDOW_START + timedelta(days=1),
            metadata=_account(999, "workspace-b-secret"),
            source_author="workspace-b-secret",
        )
        await session.commit()
    return world


async def _insights(
    engine: AsyncEngine,
    world: InsightsWorld,
    *,
    workspace_context: bool = True,
    **kwargs,
) -> dict:
    ws = world.data.target_workspace_id
    context = ws if workspace_context else None
    async with await _session(engine, world.data.viewer_id, context) as session:
        return await github_insights(
            session,
            workspace_id=ws,
            since=WINDOW_START,
            until=WINDOW_END,
            **kwargs,
        )


async def test_counts_each_published_artifact_once_and_reports_all_types(
    supabase_engine: AsyncEngine, insights_world: InsightsWorld
) -> None:
    result = await _insights(supabase_engine, insights_world)
    by_name = {item["name"]: item for item in result["contributors"]}
    alice = by_name["alice-latest"]
    assert {
        key: alice[key]
        for key in (
            "commits",
            "pull_requests",
            "reviews",
            "review_comments",
            "issues",
            "comments",
            "discussions",
            "total",
        )
    } == {
        "commits": 1,
        "pull_requests": 1,
        "reviews": 1,
        "review_comments": 1,
        "issues": 1,
        "comments": 1,
        "discussions": 1,
        "total": 7,
    }
    assert alice["live_memories"] == 1
    assert result["totals"] == {
        "commits": 3,
        "pull_requests": 1,
        "reviews": 1,
        "review_comments": 1,
        "issues": 1,
        "comments": 1,
        "discussions": 1,
        "shared": 2,
        "total": 9,
    }


async def test_revision_three_unpublished_and_anchor_clone_do_not_inflate_counts(
    supabase_engine: AsyncEngine, insights_world: InsightsWorld
) -> None:
    result = await _insights(supabase_engine, insights_world, repo=ACME_REPO)
    alice = next(item for item in result["contributors"] if item["github_user_id"] == 101)
    assert alice["commits"] == 1
    assert alice["issues"] == 1
    assert alice["total"] == 6
    assert alice["live_memories"] == 1


async def test_shared_and_git_name_authors_are_unresolved_and_never_linked(
    supabase_engine: AsyncEngine, insights_world: InsightsWorld
) -> None:
    result = await _insights(supabase_engine, insights_world)
    by_name = {item["name"]: item for item in result["contributors"]}
    assert by_name["Ghost"]["unresolved"] is True
    assert by_name["Ghost"]["linked_user"] is None
    assert by_name["Ghost"]["login"] is None
    assert by_name["Ghost"]["commits"] == 1
    for label in ("Alice Shared", "Co Author"):
        assert by_name[label]["shared"] == 1
        assert by_name[label]["total"] == 0
        assert by_name[label]["unresolved"] is True
        assert by_name[label]["linked_user"] is None
        assert by_name[label]["login"] is None


async def test_admin_link_coverage_and_disconnected_connector_filter(
    supabase_engine: AsyncEngine, insights_world: InsightsWorld
) -> None:
    result = await _insights(supabase_engine, insights_world)
    by_id = {item["github_user_id"]: item for item in result["contributors"]}
    assert by_id[101]["linked_user"] == {
        "user_id": insights_world.data.owner_id,
        "display_name": "Linked Owner",
    }
    assert by_id[202]["linked_user"] is None
    assert 303 not in by_id
    assert result["coverage"] == {
        "connectors": 1,
        "repos": 2,
        "scopes_total": 3,
        "scopes_incomplete": 1,
    }

    included = await _insights(
        supabase_engine, insights_world, include_disconnected=True
    )
    assert any(item["github_user_id"] == 303 for item in included["contributors"])
    assert included["coverage"] == {
        "connectors": 2,
        "repos": 3,
        "scopes_total": 4,
        "scopes_incomplete": 2,
    }


async def test_workspace_scope_holds_with_and_without_explicit_rls_workspace_context(
    supabase_engine: AsyncEngine, insights_world: InsightsWorld
) -> None:
    for workspace_context in (False, True):
        result = await _insights(
            supabase_engine, insights_world, workspace_context=workspace_context
        )
        assert all(
            item["github_user_id"] != 999 for item in result["contributors"]
        )
        assert all(item["name"] != "workspace-b-secret" for item in result["contributors"])


async def test_non_member_gets_404_and_viewer_can_read(
    supabase_engine: AsyncEngine, insights_world: InsightsWorld
) -> None:
    ws = insights_world.data.target_workspace_id
    outsider = _user(insights_world.data.other_workspace_member_id)
    async with await _session(
        supabase_engine, insights_world.data.other_workspace_member_id
    ) as session:
        with patch.object(api, "enforce_rate_limit", AsyncMock()):
            with pytest.raises(WorkspaceNotFoundError):
                await api.get_github_insights(
                    ws,
                    session,
                    outsider,
                    None,
                    None,
                    WINDOW_START,
                    WINDOW_END,
                    False,
                )

    viewer = _user(insights_world.data.viewer_id)
    async with await _session(supabase_engine, viewer.user_id) as session:
        with patch.object(api, "enforce_rate_limit", AsyncMock()) as rate_limit:
            response = await api.get_github_insights(
                ws,
                session,
                viewer,
                insights_world.connector_id,
                ACME_REPO,
                WINDOW_START,
                WINDOW_END,
                False,
            )
    assert response.coverage.connectors == 1
    rate_limit.assert_awaited_once()


async def test_window_repo_validation_and_order_are_deterministic(
    supabase_engine: AsyncEngine, insights_world: InsightsWorld
) -> None:
    first = await _insights(supabase_engine, insights_world, repo=ACME_REPO)
    second = await _insights(supabase_engine, insights_world, repo=ACME_REPO)
    first_names = [item["name"] for item in first["contributors"]]
    assert first_names == [item["name"] for item in second["contributors"]]
    assert first_names[:3] == ["alice", "bob", "Ghost"]
    assert first["coverage"] == {
        "connectors": 1,
        "repos": 1,
        "scopes_total": 2,
        "scopes_incomplete": 1,
    }
    assert first["totals"]["discussions"] == 0

    ws = insights_world.data.target_workspace_id
    async with await _session(supabase_engine, insights_world.data.viewer_id, ws) as session:
        narrow = await github_insights(
            session,
            workspace_id=ws,
            since=WINDOW_START + timedelta(days=6, hours=12),
            until=WINDOW_START + timedelta(days=7, hours=12),
        )
        assert [item["name"] for item in narrow["contributors"]] == ["bob", "Ghost"]
        with pytest.raises(ValidationError):
            await github_insights(
                session,
                workspace_id=ws,
                since=WINDOW_END,
                until=WINDOW_START,
            )
        with pytest.raises(ValidationError):
            await github_insights(
                session,
                workspace_id=ws,
                since=WINDOW_START,
                until=WINDOW_START + timedelta(days=367),
            )
