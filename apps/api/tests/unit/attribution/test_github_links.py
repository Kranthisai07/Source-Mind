"""Admin-asserted GitHub author links: lookup and credit-time recheck (D-021).

Resolution is ONLY ``(workspace_id, numeric github_user_id)`` -> link -> an
active member with a live user row. Names, logins and e-mails are never
compared. Every lookup runs inside a SAVEPOINT and fails closed: a database
error yields "unresolved" plus an error log, never a credit and never a crash.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MODULE = "sourcemind.services.attribution.github_links"


class _Tx:
    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def __aenter__(self):
        self._log.append("savepoint")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._log.append("rollback" if exc_type else "release")
        return False


def _session(*, link=None, member=True, fail_on: str | None = None):
    events: list[str] = []
    sql_seen: list[tuple[str, dict]] = []

    async def execute(stmt, params=None, **_k):
        sql = " ".join(str(stmt).split())
        sql_seen.append((sql, dict(params or {})))
        if fail_on and fail_on in sql:
            raise RuntimeError("synthetic database failure")
        r = MagicMock()
        if "github_author_links" in sql:
            r.first.return_value = SimpleNamespace(user_id=link) if link else None
        elif "workspace_members" in sql:
            r.first.return_value = SimpleNamespace(ok=1) if member else None
        return r

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute)
    session.begin_nested = MagicMock(side_effect=lambda: _Tx(events))
    session.events = events
    session.sql_seen = sql_seen
    return session


# ── sync-time lookup ─────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_lookup_resolves_only_by_workspace_and_numeric_id() -> None:
    from sourcemind.services.attribution.github_links import resolve_github_link

    user, ws = uuid.uuid4(), uuid.uuid4()
    session = _session(link=user)

    lookup = await resolve_github_link(session, ws, 101)

    assert (lookup.user_id, lookup.status) == (user, "linked")
    assert session.events == ["savepoint", "release"]
    sql, params = session.sql_seen[0]
    assert params == {"ws": str(ws), "gid": 101}
    lowered = sql.lower()
    for forbidden in ("login", "email", "display_name", "source_author", " like ", "ilike"):
        assert forbidden not in lowered
    # Only an active, non-departed member with a live user row resolves.
    assert "status = 'active'" in sql and "departed_at IS NULL" in sql
    assert "deleted_at IS NULL" in sql


@pytest.mark.unit
async def test_lookup_without_link_is_unlinked() -> None:
    from sourcemind.services.attribution.github_links import resolve_github_link

    lookup = await resolve_github_link(_session(link=None), uuid.uuid4(), 101)

    assert (lookup.user_id, lookup.status) == (None, "unlinked")


@pytest.mark.unit
async def test_lookup_error_fails_closed_inside_a_savepoint() -> None:
    from sourcemind.services.attribution.github_links import resolve_github_link

    session = _session(fail_on="github_author_links")
    with patch(f"{MODULE}.log") as log:
        lookup = await resolve_github_link(session, uuid.uuid4(), 101)

    assert (lookup.user_id, lookup.status) == (None, "lookup_failed")
    assert session.events == ["savepoint", "rollback"]
    log.error.assert_called_once()
    assert log.error.call_args.args[0] == "github_link_lookup_failed"


@pytest.mark.unit
@pytest.mark.parametrize("bad", [None, 0, -1, True, "101"])
async def test_lookup_rejects_non_numeric_ids_without_querying(bad) -> None:
    from sourcemind.services.attribution.github_links import resolve_github_link

    session = _session(link=uuid.uuid4())
    lookup = await resolve_github_link(session, uuid.uuid4(), bad)

    assert lookup.user_id is None
    session.execute.assert_not_awaited()


# ── credit-time recheck ──────────────────────────────────────────────────────


def _meta(link_user: uuid.UUID | None, gid: int | None = 101) -> dict:
    return {
        "mode": "external",
        "source_tool": "github",
        "github_user_id": gid,
        "source_author": "alice",
        "link_user_id": str(link_user) if link_user else None,
        "resolution": "linked" if link_user else "unlinked",
    }


@pytest.mark.unit
async def test_recheck_credits_same_user_and_locks_the_link_for_share() -> None:
    from sourcemind.services.attribution.github_links import recheck_external_credit

    user = uuid.uuid4()
    session = _session(link=user, member=True)

    decision = await recheck_external_credit(session, uuid.uuid4(), _meta(user))

    assert (decision.status, decision.user_id, decision.reason) == ("credited", user, None)
    link_sql = next(sql for sql, _p in session.sql_seen if "github_author_links" in sql)
    assert link_sql.upper().endswith("FOR SHARE")


@pytest.mark.unit
@pytest.mark.parametrize(
    "link_now,member,reason",
    [
        (None, True, "link_removed"),
        ("other", True, "link_changed"),
        ("same", False, "member_inactive"),
    ],
)
async def test_recheck_unresolved_cases(link_now, member, reason) -> None:
    from sourcemind.services.attribution.github_links import recheck_external_credit

    user = uuid.uuid4()
    current = {None: None, "other": uuid.uuid4(), "same": user}[link_now]
    session = _session(link=current, member=member)

    decision = await recheck_external_credit(session, uuid.uuid4(), _meta(user))

    assert (decision.status, decision.user_id, decision.reason) == ("unresolved", None, reason)


@pytest.mark.unit
async def test_recheck_never_looks_up_when_nothing_was_linked_at_sync() -> None:
    from sourcemind.services.attribution.github_links import recheck_external_credit

    session = _session(link=uuid.uuid4())
    unlinked = await recheck_external_credit(session, uuid.uuid4(), _meta(None))
    no_account = await recheck_external_credit(session, uuid.uuid4(), _meta(None, gid=None))

    assert (unlinked.status, unlinked.reason) == ("unresolved", "unlinked_at_sync")
    assert (no_account.status, no_account.reason) == ("unresolved", "no_github_account")
    session.execute.assert_not_awaited()


@pytest.mark.unit
async def test_recheck_lookup_error_is_unresolved_and_logged() -> None:
    from sourcemind.services.attribution.github_links import recheck_external_credit

    user = uuid.uuid4()
    session = _session(link=user, fail_on="workspace_members")
    with patch(f"{MODULE}.log") as log:
        decision = await recheck_external_credit(session, uuid.uuid4(), _meta(user))

    assert (decision.status, decision.reason) == ("unresolved", "lookup_failed")
    assert session.events[-1] == "rollback"
    assert log.error.call_args.args[0] == "github_link_lookup_failed"


# ── finalization ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("credited", [True, False])
async def test_finalize_writes_every_link_of_the_document(credited: bool) -> None:
    from sourcemind.services.attribution.github_links import (
        CreditDecision,
        finalize_external_attribution,
    )

    user, doc = uuid.uuid4(), uuid.uuid4()
    decision = (
        CreditDecision("credited", user, None)
        if credited
        else CreditDecision("unresolved", None, "link_removed")
    )
    session = _session()

    await finalize_external_attribution(session, doc, decision)

    statements = session.sql_seen
    link_update = next(s for s in statements if s[0].startswith("UPDATE artifact_links"))
    assert "WHERE document_id = CAST(:doc AS uuid)" in link_update[0]
    assert "memory_id" not in link_update[0].split("WHERE")[1]
    assert link_update[1] == {"doc": str(doc), "uid": str(user) if credited else None}
    doc_update = next(s for s in statements if s[0].startswith("UPDATE documents"))
    assert "{attribution,final}" in doc_update[0]
