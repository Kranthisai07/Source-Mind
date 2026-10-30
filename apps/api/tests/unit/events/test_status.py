"""The rollout status report: the same trust rule the API applies, plus the relay's backlog."""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from sourcemind.events import status
from tests.unit.events.fakes import RoutedSession, Rows

WS_A, WS_B, WS_C = (str(uuid.uuid4()) for _ in range(3))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("published", "applied", "gap", "pending", "expected"),
    [
        (10, 12, None, 0, status.TRUSTED),
        (10, 10, None, 0, status.TRUSTED),
        (10, 9, None, 0, status.BEHIND),
        (10, 12, 7, 0, status.GAP),  # caught up, but an event was dead-lettered
        (10, None, None, 0, status.EMPTY),  # published, nothing in Neo4j
        (None, None, None, 3, status.EMPTY),
        (10, 10, None, 2, status.RELAY_BEHIND),
        (10, 5, 3, 1, status.GAP),  # a gap outranks everything else
    ],
)
def test_the_state_follows_the_rule_the_api_uses(
    published, applied, gap, pending, expected
) -> None:
    assert status.classify(published, applied, gap, pending) == expected


def _session(cursors, pending):
    return RoutedSession(
        {
            "FROM graph_publication_cursor": Rows(cursors),
            "FROM graph_outbox": Rows(pending),
        }
    )


def _neo4j(watermarks):
    async def fake(query, workspace_id, params=None):
        assert query is status.WORKSPACE_WATERMARK
        return watermarks.get(workspace_id, [])

    return patch.object(status, "run_read", fake)


@pytest.mark.unit
async def test_a_report_combines_postgres_cursor_pending_and_the_neo4j_watermark() -> None:
    session = _session(
        [{"workspace_id": WS_A, "published_seq": 10}, {"workspace_id": WS_B, "published_seq": 20}],
        [{"workspace_id": WS_B, "pending": 4, "oldest_seconds": 12.34}],
    )
    marks = {WS_A: [{"seq": 10, "gap_seq": None}], WS_B: [{"seq": 20, "gap_seq": 5}]}
    with _neo4j(marks):
        report = await status.collect(session)
    by_ws = {r.workspace_id: r for r in report}
    assert by_ws[WS_A].state == status.TRUSTED and by_ws[WS_A].pending == 0
    assert by_ws[WS_B].state == status.GAP
    assert by_ws[WS_B].gap_seq == 5 and by_ws[WS_B].pending == 4
    assert by_ws[WS_B].oldest_pending_seconds == 12.3


@pytest.mark.unit
async def test_a_workspace_with_events_waiting_but_no_cursor_is_still_listed() -> None:
    session = _session([], [{"workspace_id": WS_C, "pending": 7, "oldest_seconds": 90.0}])
    with _neo4j({}):
        (only,) = await status.collect(session)
    assert only.workspace_id == WS_C and only.published_seq is None
    assert only.pending == 7 and only.state == status.EMPTY


@pytest.mark.unit
async def test_one_workspace_can_be_asked_for_and_the_filter_is_a_parameter() -> None:
    session = _session([{"workspace_id": WS_A, "published_seq": 1}], [])
    with _neo4j({WS_A: [{"seq": 1}]}):  # a row from before gap_seq existed counts as no gap
        report = await status.collect(session, workspace_id=uuid.UUID(WS_A))
    assert report[0].state == status.TRUSTED
    cursor_sql, cursor_params = session.statements[0]
    assert "workspace_id = CAST(:ws AS uuid)" in cursor_sql and cursor_params["ws"] == WS_A
    assert WS_A not in cursor_sql, "the id must travel as a parameter, never as query text"


@pytest.mark.unit
async def test_the_report_never_writes() -> None:
    session = _session([{"workspace_id": WS_A, "published_seq": 1}], [])
    with _neo4j({WS_A: [{"seq": 1, "gap_seq": None}]}):
        await status.collect(session)
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in session.statements)


@pytest.mark.unit
def test_the_table_and_json_output() -> None:
    report = [
        status.WorkspaceStatus(WS_A, 10, 10, None, 0, 0.0, status.TRUSTED),
        status.WorkspaceStatus(WS_B, 20, 20, 5, 4, 12.3, status.GAP),
    ]
    table = status.render(report)
    assert WS_A in table and "trusted" in table and "gap: run the backfill" in table
    assert status.render([]) == "No workspace has graph events yet."
    assert json.loads(json.dumps([r.__dict__ for r in report]))[1]["gap_seq"] == 5


class _Engine:
    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    def connect(self):
        return self._Conn()

    async def dispose(self) -> None:
        return None


async def _run_with(monkeypatch, states):
    from types import SimpleNamespace

    async def noop():
        return None

    async def fake_collect(conn, *, workspace_id=None, limit=50):
        return [status.WorkspaceStatus(WS_A, 1, 1, None, 0, 0.0, s) for s in states]

    monkeypatch.setattr(
        status, "get_settings", lambda: SimpleNamespace(graph_relay_database_url="postgresql://x")
    )
    monkeypatch.setattr(status, "create_async_engine", lambda *a, **k: _Engine())
    monkeypatch.setattr(status, "init_graph", noop)
    monkeypatch.setattr(status, "close_graph", noop)
    monkeypatch.setattr(status, "collect", fake_collect)
    return await status.run(None, 50, False)


@pytest.mark.unit
async def test_the_exit_code_is_zero_only_when_everything_is_trusted(monkeypatch) -> None:
    assert await _run_with(monkeypatch, [status.TRUSTED, status.TRUSTED]) == 0
    assert await _run_with(monkeypatch, [status.TRUSTED, status.GAP]) == 1
    assert await _run_with(monkeypatch, [status.BEHIND]) == 1


@pytest.mark.unit
async def test_it_refuses_to_run_without_the_relay_database_url(monkeypatch, capsys) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        status, "get_settings", lambda: SimpleNamespace(graph_relay_database_url="")
    )
    assert await status.run(None, 50, False) == 2
    assert "GRAPH_RELAY_DATABASE_URL" in capsys.readouterr().err
