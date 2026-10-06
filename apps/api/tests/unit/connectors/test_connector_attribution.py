"""GitHub sync records the real author, never the sync initiator (D-021).

The connector hands the ArtifactLink and a sync-time attribution decision to
receive(), which writes the link in the SAME transaction as the Document and
before the ingestion task is published. The person who clicked "sync" is only
the document submitter (the worker's authorization principal).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.connectors.github.mapper import ConnectorDocument

CONNECTOR = "sourcemind.connectors.github.connector"


@dataclass(frozen=True)
class _Lookup:
    user_id: uuid.UUID | None
    status: str


def _session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    no_existing = MagicMock()
    no_existing.scalar_one_or_none.return_value = None
    no_existing.all.return_value = []  # dedup reads link state with .all()
    session.execute = AsyncMock(return_value=no_existing)
    return session


def _connector(session: AsyncMock, initiator: uuid.UUID, workspace_id: uuid.UUID):
    from sourcemind.connectors.github.connector import GitHubConnector

    config = MagicMock()
    config.id = uuid.uuid4()
    config.config = {"repos": ["acme/repo"]}
    config.last_sync_at = None
    return GitHubConnector(
        config=config,
        auth=AsyncMock(),
        session=session,
        workspace_id=workspace_id,
        user_id=initiator,
    )


def _doc(author: dict | None, source_author: str | None = "alice") -> ConnectorDocument:
    metadata: dict = {"repo": "acme/repo"}
    if author is not None:
        metadata["author"] = author
    return ConnectorDocument(
        source_tool="github",
        source_type="commit",
        source_id="acme/repo/commits/" + uuid.uuid4().hex,
        source_url="https://github.com/acme/repo/commit/x",
        source_author=source_author,
        title="t",
        content="c " + uuid.uuid4().hex,
        idempotency_key=uuid.uuid4().hex,
        metadata=metadata,
    )


async def _ingest(doc, *, lookup: _Lookup | None, initiator=None, workspace_id=None):
    initiator = initiator or uuid.uuid4()
    workspace_id = workspace_id or uuid.uuid4()
    session = _session()
    connector = _connector(session, initiator, workspace_id)
    receive = AsyncMock(
        return_value={"already_exists": False, "document_id": str(uuid.uuid4())}
    )
    resolver = AsyncMock(return_value=lookup)
    with (
        patch(f"{CONNECTOR}.receive", new=receive),
        patch(f"{CONNECTOR}.resolve_github_link", new=resolver, create=True),
    ):
        ingested = await connector._ingest(doc)
    return ingested, receive, resolver, session, initiator, workspace_id


@pytest.mark.unit
async def test_link_and_decision_ride_on_receive_not_a_later_insert() -> None:
    linked_user = uuid.uuid4()
    doc = _doc({"github_user_id": 101, "login": "alice", "kind": "github_account"})

    ingested, receive, _resolver, session, _initiator, _ws = await _ingest(
        doc, lookup=_Lookup(linked_user, "linked")
    )

    assert ingested is True
    kwargs = receive.await_args.kwargs
    link = kwargs["artifact_link"]
    assert link["source_tool"] == "github"
    assert link["source_type"] == "commit"
    assert link["source_id"] == doc.source_id
    assert link["source_author"] == "alice"
    assert link["artifact_metadata"]["author"]["github_user_id"] == 101
    # The sync-time link never carries an identity resolution.
    assert "resolved_user_id" not in link
    # Nothing is added to the session after receive(): the link is not a
    # second, racing insert any more.
    session.add.assert_not_called()


@pytest.mark.unit
async def test_initiator_is_submitter_only_and_linked_author_is_recorded() -> None:
    initiator = uuid.uuid4()
    linked_user = uuid.uuid4()
    doc = _doc({"github_user_id": 101, "login": "alice", "kind": "github_account"})

    _ok, receive, resolver, _s, _i, workspace_id = await _ingest(
        doc, lookup=_Lookup(linked_user, "linked"), initiator=initiator
    )

    kwargs = receive.await_args.kwargs
    assert kwargs["user_id"] == initiator  # submitter / authorization principal
    assert kwargs["attribution"] == {
        "mode": "external",
        "source_tool": "github",
        "github_user_id": 101,
        "source_author": "alice",
        "link_user_id": str(linked_user),
        "resolution": "linked",
    }
    assert str(initiator) not in str(kwargs["attribution"])
    resolver.assert_awaited_once()
    assert resolver.await_args.args[1:] == (workspace_id, 101)


@pytest.mark.unit
async def test_two_different_linked_authors_are_kept_apart() -> None:
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    doc_a = _doc({"github_user_id": 1, "login": "a", "kind": "github_account"}, "a")
    doc_b = _doc({"github_user_id": 2, "login": "b", "kind": "github_account"}, "b")

    _o, receive_a, *_ = await _ingest(doc_a, lookup=_Lookup(user_a, "linked"))
    _o, receive_b, *_ = await _ingest(doc_b, lookup=_Lookup(user_b, "linked"))

    assert receive_a.await_args.kwargs["attribution"]["link_user_id"] == str(user_a)
    assert receive_b.await_args.kwargs["attribution"]["link_user_id"] == str(user_b)


@pytest.mark.unit
async def test_unlinked_author_is_recorded_as_unresolved() -> None:
    doc = _doc({"github_user_id": 101, "login": "alice", "kind": "github_account"})

    _ok, receive, *_ = await _ingest(doc, lookup=_Lookup(None, "unlinked"))

    attribution = receive.await_args.kwargs["attribution"]
    assert attribution["link_user_id"] is None
    assert attribution["resolution"] == "unlinked"


@pytest.mark.unit
async def test_lookup_failure_is_recorded_as_unresolved() -> None:
    doc = _doc({"github_user_id": 101, "login": "alice", "kind": "github_account"})

    _ok, receive, *_ = await _ingest(doc, lookup=_Lookup(None, "lookup_failed"))

    attribution = receive.await_args.kwargs["attribution"]
    assert attribution["link_user_id"] is None
    assert attribution["resolution"] == "lookup_failed"


@pytest.mark.unit
@pytest.mark.parametrize(
    "author",
    [
        # git display-name fallback that happens to equal a member's name
        {"github_user_id": None, "login": None, "kind": "git_name"},
        # discussion: login only, no numeric id
        None,
    ],
)
async def test_name_or_login_never_triggers_identity_resolution(author) -> None:
    doc = _doc(author, source_author="Initiator Display Name")

    _ok, receive, resolver, *_ = await _ingest(doc, lookup=_Lookup(uuid.uuid4(), "linked"))

    resolver.assert_not_awaited()
    attribution = receive.await_args.kwargs["attribution"]
    assert attribution["github_user_id"] is None
    assert attribution["link_user_id"] is None
    assert attribution["resolution"] == "no_github_account"


@pytest.mark.unit
async def test_duplicate_artifact_is_not_reingested() -> None:
    session = _session()
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = MagicMock()
    existing.all.return_value = [
        MagicMock(document_id=uuid.uuid4(), ingestion_status="completed", deleted_at=None)
    ]
    session.execute = AsyncMock(return_value=existing)
    connector = _connector(session, uuid.uuid4(), uuid.uuid4())
    receive = AsyncMock()
    with patch(f"{CONNECTOR}.receive", new=receive):
        assert await connector._ingest(_doc(None)) is False
    receive.assert_not_awaited()
