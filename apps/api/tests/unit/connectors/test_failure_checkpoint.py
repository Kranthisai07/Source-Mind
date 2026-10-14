"""A failed handoff never moves the discovery position past the failing item (C4).

Replaces the old ``last_sync_at`` checkpoint regression: the position is now the
per-scope resume key, written in the handoff transaction (via ``pre_commit``) or, for
a poison item, in the transaction that records its quarantine.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sourcemind.connectors.github.contracts import Discovered, ScopeState
from sourcemind.connectors.github.mapper import ConnectorDocument
from sourcemind.services.connectors.sync_state import LeaseToken

CONNECTOR = "sourcemind.connectors.github.connector"
SS = "sourcemind.services.connectors.sync_state"
TS = datetime(2026, 10, 1, tzinfo=UTC)


def _item(n: int) -> Discovered:
    sid = f"acme/repo/commits/{n:040d}"
    doc = ConnectorDocument(
        source_tool="github",
        source_type="commit",
        source_id=sid,
        title="t",
        content=f"commit {n}",
        idempotency_key=ConnectorDocument.make_key("github", "commit", sid),
        metadata={},
    )
    return Discovered(document=doc, position=(TS, sid))


def _connector():
    from sourcemind.connectors.github.connector import GitHubConnector

    session = AsyncMock()
    session.add = MagicMock()
    settings = SimpleNamespace(connector_lease_seconds=600, connector_max_handoff_attempts=3)
    connector = GitHubConnector(
        config=SimpleNamespace(id=uuid.uuid4()),
        auth=SimpleNamespace(),
        session=session,
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        client=object(),
        settings=settings,
    )
    connector._token = LeaseToken(connector._config.id, 1, "t")
    connector._revision_aware = True
    return connector


async def _handle(connector, item, *, receiver, work_items, failures=None):
    from sourcemind.connectors.github.connector import _Scope

    scope = _Scope("acme/repo", "commits", ScopeState(), None)
    counts = {"found": 0, "new": 0, "updated": 0, "skipped": 0, "failed": 0, "quarantined": 0}
    connector._work_items = work_items
    cursor = AsyncMock()
    failure = AsyncMock(side_effect=failures or [(1, "pending")])
    with (
        patch(f"{CONNECTOR}.receive", new=receiver),
        patch(f"{CONNECTOR}.resolve_github_link", new=AsyncMock()),
        patch(f"{SS}.renew_lease", new=AsyncMock()),
        patch(f"{SS}.upsert_cursor", new=cursor),
        patch(f"{SS}.record_work_failure", new=failure),
        patch(f"{SS}.mark_work_done", new=AsyncMock()),
    ):
        outcome = await connector._handle_item(scope, item, counts)
    return outcome, counts, cursor, failure


@pytest.mark.unit
async def test_success_writes_the_resume_key_inside_pre_commit() -> None:
    connector = _connector()

    async def receiver(**kwargs):
        await kwargs["pre_commit"](kwargs["session"])
        return {"already_exists": False, "document_id": str(uuid.uuid4()), "revision_seq": 1}

    outcome, counts, cursor, _f = await _handle(
        connector, _item(1), receiver=AsyncMock(side_effect=receiver), work_items={}
    )

    assert outcome == "ok" and counts["new"] == 1
    cursor.assert_awaited_once()  # written once, from pre_commit
    assert cursor.await_args.kwargs["resume_id"].endswith("1")


@pytest.mark.unit
async def test_failure_before_commit_leaves_the_key_and_stops_the_scope() -> None:
    connector = _connector()

    outcome, counts, cursor, failure = await _handle(
        connector,
        _item(2),
        receiver=AsyncMock(side_effect=RuntimeError("receiver exploded")),
        work_items={},
    )

    assert outcome == "stop"
    assert counts["failed"] == 1 and counts["quarantined"] == 0
    failure.assert_awaited_once()
    cursor.assert_not_awaited()  # position untouched
    connector._session.rollback.assert_awaited()


@pytest.mark.unit
async def test_third_failure_quarantines_and_lets_the_key_pass() -> None:
    connector = _connector()
    item = _item(3)
    key = ("acme/repo", "commits", item.document.source_id)

    outcome, counts, cursor, _f = await _handle(
        connector,
        item,
        receiver=AsyncMock(side_effect=RuntimeError("poison")),
        work_items={key: (2, "pending")},
        failures=[(3, "quarantined")],
    )

    assert outcome == "ok"  # the scope proceeds past the poison item
    assert counts["quarantined"] == 1
    cursor.assert_awaited_once()  # key advanced in the quarantine transaction


@pytest.mark.unit
async def test_already_quarantined_item_is_passed_without_a_handoff() -> None:
    connector = _connector()
    item = _item(4)
    key = ("acme/repo", "commits", item.document.source_id)
    receiver = AsyncMock()

    outcome, counts, cursor, _f = await _handle(
        connector, item, receiver=receiver, work_items={key: (3, "quarantined")}
    )

    assert outcome == "ok" and counts["skipped"] == 1
    receiver.assert_not_awaited()
    cursor.assert_awaited_once()
