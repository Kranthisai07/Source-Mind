"""Unit tests for retire_document_memories classification (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sourcemind.services.memory.retire import (
    STALE_REASON_FOR,
    _components,
    retire_document_memories,
)

WS, DOC = uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _row(parent=None, current=True, deleted=None, stale=None):
    return SimpleNamespace(
        id=uuid.uuid4(), parent_memory_id=parent, current_version=current,
        deleted_at=deleted, source_stale_at=stale,
    )


class _Session:
    def __init__(self, rows, touched=()):
        self.rows, self.touched, self.updates = rows, touched, []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FOR NO KEY UPDATE" in sql:
            res = MagicMock()
            res.all.return_value = self.rows
            return res
        if "FROM attribution_edits" in sql:
            return [SimpleNamespace(memory_id=m) for m in self.touched]
        self.updates.append((sql, params))
        return [SimpleNamespace(id=uuid.UUID(i)) for i in params["ids"]]


def test_reason_mapping() -> None:
    assert STALE_REASON_FOR == {
        "source_superseded": "source_revised",
        "source_deleted": "source_deleted",
        "connector_removed": "connector_removed",
    }


def test_components_group_by_parent() -> None:
    a = _row()
    b = _row(parent=a.id, current=False)
    c = _row(parent=b.id)
    d = _row()
    groups = sorted(_components([a, b, c, d]), key=len)
    assert [len(g) for g in groups] == [1, 3]


async def test_unknown_reason_rejected() -> None:
    with pytest.raises(ValueError):
        await retire_document_memories(
            _Session([]), workspace_id=WS, document_id=DOC, reason="user_deleted", now=NOW
        )


async def test_no_rows_is_noop() -> None:
    s = _Session([])
    out = await retire_document_memories(
        s, workspace_id=WS, document_id=DOC, reason="source_deleted", now=NOW
    )
    assert out.retired_ids == [] and out.flagged_ids == [] and s.updates == []


async def test_classification() -> None:
    machine = _row()
    root = _row(current=False)
    head = _row(parent=root.id)
    edited_in_place = _row()  # single row but a non-create edit event exists
    already_deleted = _row(deleted=NOW)
    already_flagged = _row(parent=root.id, stale=NOW)
    s = _Session(
        [machine, root, head, edited_in_place, already_deleted, already_flagged],
        touched=[edited_in_place.id],
    )
    out = await retire_document_memories(
        s, workspace_id=WS, document_id=DOC, reason="source_superseded", now=NOW
    )
    assert out.retired_ids == [machine.id]
    assert set(out.flagged_ids) == {head.id, edited_in_place.id}
    assert already_flagged.id not in out.flagged_ids  # idempotent: not re-stamped
    assert all(
        "attribution" not in sql.split("UPDATE", 1)[1].split("SET")[0] for sql, _ in s.updates
    )
