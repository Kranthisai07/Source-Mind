"""PATCH /v1/memories/{id} keeps contribution history across versions.

Route-level coverage against disposable PostgreSQL. Every request goes through
the real FastAPI app with real authorization (require_memory_access), real
row-level security, real create_new_version / recompute_attribution /
carry_forward_attribution and real persistence. Only two things are replaced:
the identity of the caller (a header selects one of the seeded users) and the
5-signal scorer (a deterministic equal-split fake that RECORDS the events it is
given, because spaCy cannot load on this Python and because these tests are
about what history reaches the scorer, not about the numbers it returns).

No test asserts a share percentage for any editor. Scoring policy is a separate,
known-defective area and is deliberately not exercised here.

Refusals (D-022, temporary): a substantive edit that would discard contributors
who exist only as snapshot rows, and a tags-only update whose snapshot is
ambiguous, both answer 409 / SM034 and are asserted to write nothing.

Requires SECURITY_TEST_ALLOW_DISPOSABLE=1 and the disposable TEST_DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from sourcemind.core.database import (
    get_db_session,
    set_rls_user_context,
    set_rls_workspace_context,
)
from sourcemind.core.dependencies import AuthenticatedUser, get_current_user, get_openai_client
from sourcemind.services.attribution import engine as attribution_engine
from sourcemind.services.attribution import scorer as scorer_module
from sourcemind.services.attribution.engine import create_initial_attribution
from sourcemind.services.attribution.scorer import AttributionScorer, ContributorScore
from tests.integration.test_security_foundation_real_db import (
    SecurityTenantData,
    _seed_security_tenants,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SIGNALS = ("char_diff_score", "semantic_score", "temporal_score", "structural_score")


class _RecordingScorer:
    """Equal-split scorer that records every event list it receives."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    def compute_scores(self, edits):
        self.calls.append(list(edits))
        users = list(dict.fromkeys(e.user_id for e in edits))
        return [
            SimpleNamespace(
                user_id=u,
                contribution_weight=1 / len(users),
                char_diff_score=0.5,
                semantic_score=0.5,
                temporal_score=0.5,
                structural_score=0.5,
                approval_score=0.0,
            )
            for u in users
        ]


@pytest.fixture
def scorer(monkeypatch: pytest.MonkeyPatch) -> _RecordingScorer:
    s = _RecordingScorer()
    monkeypatch.setattr(attribution_engine, "get_scorer", lambda: s)
    monkeypatch.setattr(scorer_module, "get_scorer", lambda: s)
    return s


@pytest_asyncio.fixture
async def db_engine(supabase_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(supabase_url, pool_size=4, max_overflow=0, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def tenants(db_engine: AsyncEngine) -> SecurityTenantData:
    return await _seed_security_tenants(db_engine)


async def _session(engine: AsyncEngine, user_id: uuid.UUID, workspace_id: uuid.UUID):
    session = AsyncSession(engine, expire_on_commit=False)
    await set_rls_user_context(session, user_id)
    await set_rls_workspace_context(session, workspace_id)
    return session


@pytest_asyncio.fixture
async def client(
    db_engine: AsyncEngine, tenants: SecurityTenantData
) -> AsyncIterator[httpx.AsyncClient]:
    """App client. X-Test-User picks the caller; each request owns its session."""
    from sourcemind.main import create_app

    app = create_app()
    known = dict(tenants.clerk_ids)

    async def override_db(request: Request) -> AsyncIterator[AsyncSession]:
        user_id = uuid.UUID(request.headers["X-Test-User"])
        async with AsyncSession(db_engine, expire_on_commit=False) as session:
            await set_rls_user_context(session, user_id)
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_user(request: Request) -> AuthenticatedUser:
        user_id = uuid.UUID(request.headers["X-Test-User"])
        return AuthenticatedUser(
            user_id=user_id,
            clerk_id=known.get(user_id, "test"),
            email=f"{user_id}@example.invalid",
        )

    async def no_openai() -> None:
        return None

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_openai_client] = no_openai
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://history.test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# ── helpers ──────────────────────────────────────────────────────────────────


async def _seed_memory(
    engine: AsyncEngine,
    data: SecurityTenantData,
    creator: uuid.UUID,
    content: str,
    *,
    workspace_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    tags: list[str] | None = None,
) -> uuid.UUID:
    """A memory created by ``creator`` through the real creation path."""
    ws = workspace_id or data.target_workspace_id
    memory_id = uuid.uuid4()
    session = await _session(engine, creator, ws)
    async with session:
        await session.execute(
            text(
                "INSERT INTO memories (id, workspace_id, document_id, content, content_hash, "
                "version, current_version, tags) VALUES (CAST(:id AS uuid), CAST(:ws AS uuid), "
                "CAST(:doc AS uuid), :c, :h, 1, TRUE, :tags)"
            ),
            {
                "id": str(memory_id),
                "ws": str(ws),
                "doc": str(document_id) if document_id else None,
                "c": content,
                "h": uuid.uuid4().hex,
                "tags": tags or [],
            },
        )
        await create_initial_attribution(session, memory_id, creator, content, "text")
        await session.commit()
    return memory_id


async def _patch(
    client: httpx.AsyncClient,
    user: uuid.UUID,
    memory_id: uuid.UUID,
    content: str,
    tags: list[str] | None = None,
    key: str | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"content": content}
    if tags is not None:
        body["tags"] = tags
    return await client.patch(
        f"/v1/memories/{memory_id}",
        json=body,
        headers={"X-Test-User": str(user), "Idempotency-Key": key or str(uuid.uuid4())},
    )


async def _get(client, user, memory_id) -> httpx.Response:
    return await client.get(
        f"/v1/memories/{memory_id}?include_attribution=true",
        headers={"X-Test-User": str(user)},
    )


async def _snapshot(engine, data, memory_id, workspace_id=None) -> list[tuple]:
    session = await _session(engine, data.owner_id, workspace_id or data.target_workspace_id)
    async with session:
        rows = await session.execute(
            text(
                "SELECT user_id::text, contribution_weight, char_diff_score, semantic_score, "
                "temporal_score, structural_score, approval_score, trigger_action, "
                "edit_id::text FROM attributions WHERE memory_id = CAST(:m AS uuid) "
                "ORDER BY user_id, created_at, id"
            ),
            {"m": str(memory_id)},
        )
        return [tuple(r) for r in rows]


async def _edits(engine, data, memory_ids, workspace_id=None) -> list[tuple]:
    session = await _session(engine, data.owner_id, workspace_id or data.target_workspace_id)
    async with session:
        rows = await session.execute(
            text(
                "SELECT editor_id::text, edit_position, content_after FROM attribution_edits "
                "WHERE memory_id = ANY(CAST(:ids AS uuid[])) ORDER BY edit_position, created_at"
            ),
            {"ids": [str(m) for m in memory_ids]},
        )
        return [tuple(r) for r in rows]


async def _chain_state(engine, data, root_id) -> list[tuple]:
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        rows = await session.execute(
            text(
                "WITH RECURSIVE c AS (SELECT id FROM memories WHERE id = CAST(:r AS uuid) "
                "UNION ALL SELECT m.id FROM memories m JOIN c ON m.parent_memory_id = c.id) "
                "SELECT m.id::text, m.version, m.current_version FROM memories m "
                "JOIN c ON c.id = m.id ORDER BY m.version"
            ),
            {"r": str(root_id)},
        )
        return [tuple(r) for r in rows]


def _contributors(resp: httpx.Response) -> set[uuid.UUID]:
    return {uuid.UUID(c["user"]["id"]) for c in resp.json()["data"]["attribution"] or []}


# ── 1/2/3/4: history across versions through PATCH ───────────────────────────


async def test_alice_bob_alice_history_is_preserved_once_in_order(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Alice wrote this first.")
    v1_snapshot = await _snapshot(db_engine, tenants, v1)

    r2 = await _patch(client, bob, v1, "Alice wrote this first. Bob added a clause.")
    assert r2.status_code == 200, r2.text
    v2 = uuid.UUID(r2.json()["data"]["id"])
    assert [(e.user_id, e.edit_position) for e in scorer.calls[-1]] == [
        (str(alice), 1),
        (str(bob), 2),
    ]

    r3 = await _patch(client, alice, v2, "Alice rewrote it after Bob's clause.")
    assert r3.status_code == 200, r3.text
    v3 = uuid.UUID(r3.json()["data"]["id"])

    assert [(e.user_id, e.edit_position) for e in scorer.calls[-1]] == [
        (str(alice), 1),
        (str(bob), 2),
        (str(alice), 3),
    ]
    stored = await _edits(db_engine, tenants, [v1, v2, v3])
    assert [(e[0], e[1]) for e in stored] == [(str(alice), 1), (str(bob), 2), (str(alice), 3)]

    # Current version exposes the computed contributor set, not just the editor.
    current = await _get(client, bob, v3)
    assert current.status_code == 200
    assert _contributors(current) == {alice, bob}
    # Older versions' recorded attribution is untouched.
    assert await _snapshot(db_engine, tenants, v1) == v1_snapshot
    old = await _get(client, bob, v1)
    assert _contributors(old) == {alice}


# ── 5/6/7: unchanged, tags-only, equal-length ────────────────────────────────


async def test_exact_unchanged_save_is_a_noop(client, db_engine, tenants, scorer) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Stable text.", tags=["x"])
    before_snapshot = await _snapshot(db_engine, tenants, v1)
    before_calls = len(scorer.calls)

    resp = await _patch(client, bob, v1, "Stable text.")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["id"] == str(v1)
    assert resp.json()["data"]["version"] == 1

    assert len(scorer.calls) == before_calls
    assert len(await _edits(db_engine, tenants, [v1])) == 1
    assert len(await _chain_state(db_engine, tenants, v1)) == 1
    assert await _snapshot(db_engine, tenants, v1) == before_snapshot


async def test_tags_only_update_keeps_attribution_readable_and_adds_no_event(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Tagged text.")
    v2 = uuid.UUID(
        (await _patch(client, bob, v1, "Tagged text. Plus Bob.")).json()["data"]["id"]
    )
    parent_snapshot = await _snapshot(db_engine, tenants, v2)
    calls = len(scorer.calls)

    resp = await _patch(client, alice, v2, "Tagged text. Plus Bob.", tags=["new", "tags"])
    assert resp.status_code == 200, resp.text
    v3 = uuid.UUID(resp.json()["data"]["id"])
    assert v3 != v2 and resp.json()["data"]["tags"] == ["new", "tags"]

    assert len(scorer.calls) == calls  # no scorer run, hence no new event
    assert len(await _edits(db_engine, tenants, [v1, v2, v3])) == 2
    assert await _snapshot(db_engine, tenants, v3) == parent_snapshot
    assert _contributors(await _get(client, alice, v3)) == {alice, bob}

    # Repeated carry-forward still never becomes an event.
    again = await _patch(client, alice, v3, "Tagged text. Plus Bob.", tags=["third"])
    v4 = uuid.UUID(again.json()["data"]["id"])
    assert await _snapshot(db_engine, tenants, v4) == parent_snapshot
    assert len(await _edits(db_engine, tenants, [v1, v2, v3, v4])) == 2


async def test_equal_length_replacement_is_a_genuine_edit(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    original, replacement = "abcd efgh ijkl", "wxyz mnop qrst"
    assert len(original) == len(replacement) and original != replacement
    v1 = await _seed_memory(db_engine, tenants, alice, original)

    resp = await _patch(client, bob, v1, replacement)
    assert resp.status_code == 200, resp.text
    v2 = uuid.UUID(resp.json()["data"]["id"])
    assert v2 != v1
    assert (await _edits(db_engine, tenants, [v1, v2]))[-1][0] == str(bob)


# ── 8: retries ───────────────────────────────────────────────────────────────


async def test_retried_content_patch_creates_no_duplicate_event_or_version(
    client, db_engine, tenants, scorer
) -> None:
    """Records ACTUAL behaviour. A successful replay is NOT provided.

    The second request targets the version that is no longer current, so it is
    refused; nothing is duplicated. Whether the original response should be
    replayed for a matching request is unresolved (D-022).
    """
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Retry me.")
    key = str(uuid.uuid4())

    first = await _patch(client, bob, v1, "Retry me, edited.", key=key)
    second = await _patch(client, bob, v1, "Retry me, edited.", key=key)

    assert first.status_code == 200
    assert second.status_code == 404  # stale id; not a successful replay
    chain = await _chain_state(db_engine, tenants, v1)
    assert [c[1] for c in chain] == [1, 2] and sum(1 for c in chain if c[2]) == 1
    assert len(await _edits(db_engine, tenants, [uuid.UUID(c[0]) for c in chain])) == 2


async def test_retried_metadata_only_patch_is_noop_on_new_id_and_404_on_stale_id(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Meta text.")
    first = await _patch(client, bob, v1, "Meta text.", tags=["t"])
    v2 = uuid.UUID(first.json()["data"]["id"])

    stale = await _patch(client, bob, v1, "Meta text.", tags=["t"])
    on_new = await _patch(client, bob, v2, "Meta text.", tags=["t"])

    assert stale.status_code == 404  # documented behaviour, not idempotency
    assert on_new.status_code == 200 and on_new.json()["data"]["id"] == str(v2)
    chain = await _chain_state(db_engine, tenants, v1)
    assert [c[1] for c in chain] == [1, 2]
    assert len(await _edits(db_engine, tenants, [v1, v2])) == 1


# ── authorization precedes every return path ─────────────────────────────────


@pytest.mark.parametrize("who", ["viewer_id", "zero_membership_id", "other_workspace_member_id"])
async def test_unauthorized_callers_cannot_use_the_noop_or_tags_paths(
    client, db_engine, tenants, scorer, who
) -> None:
    alice = tenants.admin_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Guarded text.")
    caller = getattr(tenants, who)

    unchanged = await _patch(client, caller, v1, "Guarded text.")
    tags = await _patch(client, caller, v1, "Guarded text.", tags=["sneaky"])

    assert unchanged.status_code in {403, 404}
    assert tags.status_code in {403, 404}
    assert len(await _chain_state(db_engine, tenants, v1)) == 1


# ── 9: histories never mix ───────────────────────────────────────────────────


async def test_sibling_memories_and_other_workspaces_never_mix(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    doc_id = uuid.uuid4()
    session = await _session(db_engine, tenants.owner_id, tenants.target_workspace_id)
    async with session:
        await session.execute(
            text(
                "INSERT INTO documents (id, workspace_id, submitter_id, source_type, "
                "sha256_hash, ingestion_status) VALUES (CAST(:d AS uuid), CAST(:w AS uuid), "
                "CAST(:s AS uuid), 'text', :h, 'completed')"
            ),
            {
                "d": str(doc_id),
                "w": str(tenants.target_workspace_id),
                "s": str(tenants.owner_id),
                "h": uuid.uuid4().hex + uuid.uuid4().hex,
            },
        )
        await session.commit()
    edited = await _seed_memory(db_engine, tenants, alice, "Edited one.", document_id=doc_id)
    sibling = await _seed_memory(
        db_engine, tenants, tenants.owner_id, "Sibling, same document.", document_id=doc_id
    )
    other_ws = await _seed_memory(
        db_engine,
        tenants,
        tenants.other_workspace_member_id,
        "Other workspace.",
        workspace_id=tenants.other_workspace_id,
    )
    sibling_before = await _snapshot(db_engine, tenants, sibling)
    other_before = await _snapshot(db_engine, tenants, other_ws, tenants.other_workspace_id)

    resp = await _patch(client, bob, edited, "Edited one, then changed.")
    assert resp.status_code == 200, resp.text

    seen = {e.user_id for e in scorer.calls[-1]}
    assert seen == {str(alice), str(bob)}  # not the sibling's or other workspace's authors
    assert await _snapshot(db_engine, tenants, sibling) == sibling_before
    assert (
        await _snapshot(db_engine, tenants, other_ws, tenants.other_workspace_id) == other_before
    )


# ── 10: unresolved external origin survives an edit ──────────────────────────


async def test_unresolved_origin_survives_a_real_edit_without_crediting_the_initiator(
    client, db_engine, tenants, scorer
) -> None:
    initiator, bob = tenants.admin_id, tenants.member_id
    doc_id, memory_id = uuid.uuid4(), uuid.uuid4()
    session = await _session(db_engine, tenants.owner_id, tenants.target_workspace_id)
    async with session:
        await session.execute(
            text(
                "INSERT INTO documents (id, workspace_id, submitter_id, source_type, "
                "sha256_hash, ingestion_status) VALUES (CAST(:d AS uuid), CAST(:w AS uuid), "
                "CAST(:s AS uuid), 'github', :h, 'completed')"
            ),
            {
                "d": str(doc_id),
                "w": str(tenants.target_workspace_id),
                "s": str(initiator),  # the sync initiator is the submitter
                "h": uuid.uuid4().hex + uuid.uuid4().hex,
            },
        )
        await session.execute(
            text(
                "INSERT INTO memories (id, workspace_id, document_id, content, content_hash, "
                "version, current_version) VALUES (CAST(:m AS uuid), CAST(:w AS uuid), "
                "CAST(:d AS uuid), 'External fact.', :h, 1, TRUE)"
            ),
            {
                "m": str(memory_id),
                "w": str(tenants.target_workspace_id),
                "d": str(doc_id),
                "h": uuid.uuid4().hex,
            },
        )
        await session.execute(
            text(
                "INSERT INTO artifact_links (workspace_id, document_id, source_tool, "
                "source_type, source_id, source_author, resolved_user_id) VALUES "
                "(CAST(:w AS uuid), CAST(:d AS uuid), 'github', 'commit', :sid, "
                "'ghost-author', NULL)"
            ),
            {
                "w": str(tenants.target_workspace_id),
                "d": str(doc_id),
                "sid": uuid.uuid4().hex,
            },
        )
        await session.commit()

    resp = await _patch(client, bob, memory_id, "External fact, corrected by Bob.")
    assert resp.status_code == 200, resp.text
    new_id = uuid.UUID(resp.json()["data"]["id"])

    detail = await _get(client, bob, new_id)
    data = detail.json()["data"]
    assert data["unresolved_author"] == {
        "status": "unresolved",
        "source_author": "ghost-author",
        "source_tool": "github",
    }
    assert _contributors(detail) == {bob}  # editor credited as editor only
    assert initiator not in _contributors(detail)
    assert await _snapshot(db_engine, tenants, memory_id) == []


# ── snapshot-only contributors and ambiguous snapshots (D-022 refusals) ──────


async def _write_state(engine, data, root_id) -> dict[str, Any]:
    """Everything a refused request must leave untouched."""
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        chain = (
            await session.execute(
                text(
                    "WITH RECURSIVE c AS (SELECT id FROM memories WHERE id = CAST(:r AS uuid) "
                    "UNION ALL SELECT m.id FROM memories m JOIN c ON m.parent_memory_id = c.id) "
                    "SELECT m.id::text, m.version, m.current_version, m.tags, m.content "
                    "FROM memories m JOIN c ON c.id = m.id ORDER BY m.version"
                ),
                {"r": str(root_id)},
            )
        ).all()
        ids = [row[0] for row in chain]
        edits = (
            await session.execute(
                text(
                    "SELECT count(*) FROM attribution_edits "
                    "WHERE memory_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": ids},
            )
        ).scalar_one()
        attributions = (
            await session.execute(
                text(
                    "SELECT count(*) FROM attributions "
                    "WHERE memory_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": ids},
            )
        ).scalar_one()
    return {
        "chain": [tuple(r) for r in chain],
        "edits": edits,
        "attributions": attributions,
    }


def _assert_state_conflict(resp: httpx.Response) -> None:
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "SM034", resp.text


async def _insert_merged(engine, data, users, content="Merged text.") -> uuid.UUID:
    merged = uuid.uuid4()
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        await session.execute(
            text(
                "INSERT INTO memories (id, workspace_id, content, content_hash, version, "
                "current_version) VALUES (CAST(:m AS uuid), CAST(:w AS uuid), :c, :h, 1, TRUE)"
            ),
            {
                "m": str(merged),
                "w": str(data.target_workspace_id),
                "c": content,
                "h": uuid.uuid4().hex,
            },
        )
        for user in users:
            await session.execute(
                text(
                    "INSERT INTO attributions (memory_id, user_id, contribution_weight, "
                    "trigger_action) VALUES (CAST(:m AS uuid), CAST(:u AS uuid), 0.5, 'merged')"
                ),
                {"m": str(merged), "u": str(user)},
            )
        await session.commit()
    return merged


async def test_merged_snapshot_is_preserved_by_unchanged_and_tags_only_updates(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    merged = await _insert_merged(db_engine, tenants, (alice, bob))
    original = await _snapshot(db_engine, tenants, merged)

    noop = await _patch(client, bob, merged, "Merged text.")
    assert noop.json()["data"]["id"] == str(merged)
    assert await _snapshot(db_engine, tenants, merged) == original

    tagged = await _patch(client, bob, merged, "Merged text.", tags=["kept"])
    assert tagged.status_code == 200, tagged.text
    new_id = uuid.UUID(tagged.json()["data"]["id"])
    assert await _snapshot(db_engine, tenants, new_id) == original
    assert scorer.calls == []
    assert _contributors(await _get(client, bob, new_id)) == {alice, bob}


async def test_substantive_edit_of_a_merged_memory_is_refused_with_zero_writes(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    merged = await _insert_merged(db_engine, tenants, (alice, bob))
    before = await _write_state(db_engine, tenants, merged)

    resp = await _patch(client, bob, merged, "Merged text, rewritten by Bob.")

    _assert_state_conflict(resp)
    assert await _write_state(db_engine, tenants, merged) == before
    assert scorer.calls == []
    assert _contributors(await _get(client, bob, merged)) == {alice, bob}


async def test_substantive_edit_of_a_metadata_only_descendant_of_a_merge_is_refused(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    merged = await _insert_merged(db_engine, tenants, (alice, bob))
    tagged = await _patch(client, bob, merged, "Merged text.", tags=["descendant"])
    descendant = uuid.UUID(tagged.json()["data"]["id"])
    before = await _write_state(db_engine, tenants, merged)

    resp = await _patch(client, alice, descendant, "Merged text, rewritten later.")

    _assert_state_conflict(resp)
    assert await _write_state(db_engine, tenants, merged) == before
    assert scorer.calls == []


async def test_substantive_edit_after_a_handoff_transfer_is_refused_with_zero_writes(
    client, db_engine, tenants, scorer
) -> None:
    """A transfer recipient exists only as a snapshot row, like a merge source."""
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Handed-off text.")
    session = await _session(db_engine, tenants.owner_id, tenants.target_workspace_id)
    async with session:
        await session.execute(
            text(
                "INSERT INTO attributions (memory_id, user_id, contribution_weight, "
                "trigger_action) VALUES (CAST(:m AS uuid), CAST(:u AS uuid), 0.4, 'transfer')"
            ),
            {"m": str(v1), "u": str(bob)},
        )
        await session.commit()
    before = await _write_state(db_engine, tenants, v1)

    resp = await _patch(client, alice, v1, "Handed-off text, edited.")

    _assert_state_conflict(resp)
    assert await _write_state(db_engine, tenants, v1) == before


async def _insert_tied_snapshots(engine, data, memory_id, user_id, shares) -> None:
    """Edit-linked row (create, edit_position 1) and a transfer row (no edit_id)
    for one user, forced to share created_at."""
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        stamp = (await session.execute(text("SELECT now() + interval '1 hour'"))).scalar_one()
        edit_id = (
            await session.execute(
                text(
                    "SELECT id FROM attribution_edits "
                    "WHERE memory_id = CAST(:m AS uuid) AND edit_position = 1"
                ),
                {"m": str(memory_id)},
            )
        ).scalar_one()
        for weight, action, linked in shares:
            await session.execute(
                text(
                    "INSERT INTO attributions (memory_id, user_id, contribution_weight, "
                    "trigger_action, edit_id, created_at) VALUES (CAST(:m AS uuid), "
                    "CAST(:u AS uuid), :w, :a, CAST(:e AS uuid), :t)"
                ),
                {
                    "m": str(memory_id),
                    "u": str(user_id),
                    "w": weight,
                    "a": action,
                    "e": str(edit_id) if linked else None,
                    "t": stamp,
                },
            )
        await session.commit()


async def test_tied_edit_row_and_transfer_row_with_differing_shares_refuse_tags_only(
    client, db_engine, tenants, scorer
) -> None:
    """The edit-linked row has a non-null edit_position; the transfer row has no
    edit_id; both share created_at. Chronology between them is unknown."""
    alice = tenants.admin_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Tied text.")
    await _insert_tied_snapshots(
        db_engine, tenants, v1, alice, [(0.9, "create", True), (0.3, "transfer", False)]
    )
    before = await _write_state(db_engine, tenants, v1)

    resp = await _patch(client, alice, v1, "Tied text.", tags=["refused"])

    _assert_state_conflict(resp)
    assert await _write_state(db_engine, tenants, v1) == before
    # An exact unchanged save needs no carry-forward and is still allowed.
    assert (await _patch(client, alice, v1, "Tied text.")).status_code == 200


async def test_tied_snapshots_equal_in_value_but_not_in_provenance_still_refuse(
    client, db_engine, tenants, scorer
) -> None:
    alice = tenants.admin_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Provenance text.")
    await _insert_tied_snapshots(
        db_engine, tenants, v1, alice, [(0.5, "create", True), (0.5, "transfer", False)]
    )
    before = await _write_state(db_engine, tenants, v1)

    _assert_state_conflict(await _patch(client, alice, v1, "Provenance text.", tags=["x"]))
    assert await _write_state(db_engine, tenants, v1) == before


async def test_identical_tied_duplicates_are_collapsed_not_refused(
    client, db_engine, tenants, scorer
) -> None:
    alice = tenants.admin_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Duplicate text.")
    await _insert_tied_snapshots(
        db_engine, tenants, v1, alice, [(0.7, "create", True), (0.7, "create", True)]
    )

    resp = await _patch(client, alice, v1, "Duplicate text.", tags=["fine"])

    assert resp.status_code == 200, resp.text
    carried = await _snapshot(db_engine, tenants, uuid.UUID(resp.json()["data"]["id"]))
    assert len(carried) == 1 and carried[0][1] == 0.7


# ── concurrency: independent sessions, explicit overlap ──────────────────────


@pytest.mark.parametrize("same_key", [False, True], ids=["different-keys", "same-key"])
async def test_concurrent_patches_of_one_version_settle_without_forking(
    client, db_engine, tenants, scorer, monkeypatch, same_key
) -> None:
    """Request A is held inside its transaction (row locked, uncommitted) while
    request B starts. B must be observed waiting on a lock before A is released,
    so the overlap is established by database state, not by sleeping."""
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Race me.")
    shared = str(uuid.uuid4())

    entered, release = asyncio.Event(), asyncio.Event()
    original = attribution_engine.recompute_attribution
    state = {"first": True}

    async def held(*args, **kwargs):
        if state["first"]:
            state["first"] = False
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(attribution_engine, "recompute_attribution", held)

    task_a = asyncio.create_task(
        _patch(client, alice, v1, "Race me, A.", key=shared if same_key else None)
    )
    await asyncio.wait_for(entered.wait(), timeout=15)
    task_b = asyncio.create_task(
        _patch(client, bob, v1, "Race me, B.", key=shared if same_key else None)
    )

    await _await_blocked_on_row_lock(db_engine)  # B is provably waiting on A's row lock
    release.set()
    resp_a, resp_b = await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=30)

    statuses = sorted([resp_a.status_code, resp_b.status_code])
    # Observed contract: the winner succeeds; the loser finds no current version.
    assert statuses == [200, 404], (resp_a.text, resp_b.text)
    chain = await _chain_state(db_engine, tenants, v1)
    assert [c[1] for c in chain] == [1, 2]
    assert sum(1 for c in chain if c[2]) == 1  # exactly one current version
    assert len(await _edits(db_engine, tenants, [uuid.UUID(c[0]) for c in chain])) == 2


# ── handoff transfers: provenance rule and the shared memory-row lock ────────


async def _seed_handoff_on(engine, data, memory_id, departing) -> uuid.UUID:
    """An in_progress handoff (initiated by the owner) with one assignment."""
    handoff_id = uuid.uuid4()
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        await session.execute(
            text(
                "INSERT INTO handoff_records (id, workspace_id, departing_user_id, "
                "initiated_by, tier_1_count, expires_at) VALUES (CAST(:h AS uuid), "
                "CAST(:w AS uuid), CAST(:d AS uuid), CAST(:i AS uuid), 1, "
                "now() + interval '1 day')"
            ),
            {
                "h": str(handoff_id),
                "w": str(data.target_workspace_id),
                "d": str(departing),
                "i": str(data.owner_id),
            },
        )
        await session.execute(
            text(
                "INSERT INTO handoff_assignments (handoff_id, memory_id, tier) "
                "VALUES (CAST(:h AS uuid), CAST(:m AS uuid), 1)"
            ),
            {"h": str(handoff_id), "m": str(memory_id)},
        )
        await session.commit()
    return handoff_id


async def _handoff_state(engine, data, handoff_id) -> dict[str, Any]:
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        count = (
            await session.execute(
                text("SELECT assigned_count FROM handoff_records WHERE id = CAST(:h AS uuid)"),
                {"h": str(handoff_id)},
            )
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    "SELECT memory_id::text, new_owner_id::text FROM handoff_assignments "
                    "WHERE handoff_id = CAST(:h AS uuid) ORDER BY memory_id"
                ),
                {"h": str(handoff_id)},
            )
        ).all()
    return {"assigned_count": count, "assignments": [tuple(r) for r in rows]}


async def _assign(client, tenants, handoff_id, memory_id, new_owner) -> httpx.Response:
    return await client.post(
        f"/v1/workspaces/{tenants.target_workspace_id}/handoff/assign",
        json={
            "handoff_record_id": str(handoff_id),
            "memory_id": str(memory_id),
            "new_owner_id": str(new_owner),
            "note": "history regression",
        },
        headers={"X-Test-User": str(tenants.owner_id)},
    )


async def _await_blocked_on_row_lock(engine) -> None:
    """Return once a backend is observed waiting on another transaction's row lock
    (wait_event transactionid). The overlap is proven by database state, never by
    elapsed time. pg_stat_activity.query is not used: under the extended protocol
    it still shows the previous statement while the next one is blocked."""
    waiter = AsyncSession(engine)
    try:
        for _ in range(150):
            blocked = (
                await waiter.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = "
                        "current_database() AND wait_event_type = 'Lock' "
                        "AND wait_event = 'transactionid' AND pid <> pg_backend_pid()"
                    )
                )
            ).scalar_one()
            # pg_stat_activity is cached for the life of a transaction
            # (stats_fetch_consistency = cache), so a backend that connects after
            # the first poll stays invisible until the transaction ends. End it
            # on every poll so each one sees current state.
            await waiter.rollback()
            if blocked:
                return
            await asyncio.sleep(0.1)
        activity = (
            await waiter.execute(
                text(
                    "SELECT pid, state, wait_event_type, wait_event, left(query, 90) "
                    "FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid()"
                )
            )
        ).all()
    finally:
        await waiter.close()
    pytest.fail(f"no backend was observed waiting on the memory row lock: {activity}")


async def test_transfer_to_a_user_who_also_edited_is_still_protected_from_loss(
    client, db_engine, tenants, scorer
) -> None:
    """Bob genuinely edited the chain, then received a handoff transfer. His user
    id appears in the edit history, but the transferred share is snapshot-only."""
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Chain text.")
    r2 = await _patch(client, bob, v1, "Chain text, Bob edit.")
    assert r2.status_code == 200, r2.text
    v2 = uuid.UUID(r2.json()["data"]["id"])
    handoff_id = await _seed_handoff_on(db_engine, tenants, v2, alice)

    assigned = await _assign(client, tenants, handoff_id, v2, bob)
    assert assigned.status_code == 200, assigned.text

    before = await _write_state(db_engine, tenants, v1)
    before_handoff = await _handoff_state(db_engine, tenants, handoff_id)
    events_before = len(scorer.calls)

    refused = await _patch(client, alice, v2, "Chain text, rewritten after the transfer.")
    _assert_state_conflict(refused)
    assert await _write_state(db_engine, tenants, v1) == before
    assert await _handoff_state(db_engine, tenants, handoff_id) == before_handoff
    assert len(scorer.calls) == events_before  # no event, scorer never reached

    noop = await _patch(client, alice, v2, "Chain text, Bob edit.")
    assert noop.status_code == 200 and noop.json()["data"]["id"] == str(v2)
    assert await _write_state(db_engine, tenants, v1) == before

    tagged = await _patch(client, alice, v2, "Chain text, Bob edit.", tags=["after-transfer"])
    assert tagged.status_code == 200, tagged.text
    v3 = uuid.UUID(tagged.json()["data"]["id"])
    assert len(scorer.calls) == events_before
    assert _contributors(await _get(client, alice, v3)) == {alice, bob}
    assert await _handoff_state(db_engine, tenants, handoff_id) == before_handoff


async def test_patch_that_wins_the_row_lock_makes_a_later_handoff_stale(
    client, db_engine, tenants, scorer, monkeypatch
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Lock order text.")
    handoff_id = await _seed_handoff_on(db_engine, tenants, v1, alice)
    handoff_before = await _handoff_state(db_engine, tenants, handoff_id)

    entered, release = asyncio.Event(), asyncio.Event()
    original = attribution_engine.recompute_attribution
    state = {"first": True}

    async def held(*args, **kwargs):
        if state["first"]:
            state["first"] = False
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(attribution_engine, "recompute_attribution", held)

    patch_task = asyncio.create_task(_patch(client, bob, v1, "Lock order text, edited."))
    await asyncio.wait_for(entered.wait(), timeout=15)  # PATCH holds the memory row lock
    handoff_task = asyncio.create_task(_assign(client, tenants, handoff_id, v1, bob))
    await _await_blocked_on_row_lock(db_engine)  # handoff is provably waiting
    release.set()
    patched, assigned = await asyncio.wait_for(
        asyncio.gather(patch_task, handoff_task), timeout=30
    )

    assert patched.status_code == 200, patched.text
    assert assigned.status_code == 409, assigned.text
    assert assigned.json()["error"]["code"] == "SM035", assigned.text
    v2 = uuid.UUID(patched.json()["data"]["id"])
    for memory_id in (v1, v2):
        transfers = [
            r for r in await _snapshot(db_engine, tenants, memory_id) if r[7] == "transfer"
        ]
        assert transfers == []
    assert await _handoff_state(db_engine, tenants, handoff_id) == handoff_before
    assert [(c[1], c[2]) for c in await _chain_state(db_engine, tenants, v1)] == [
        (1, False),
        (2, True),
    ]


async def test_handoff_that_wins_the_row_lock_is_seen_and_protected_by_the_patch_guard(
    client, db_engine, tenants, scorer, monkeypatch
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Handoff first text.")
    handoff_id = await _seed_handoff_on(db_engine, tenants, v1, alice)

    from sourcemind.services.memory import importance

    entered, release = asyncio.Event(), asyncio.Event()
    original = importance.recompute_importance
    state = {"first": True}

    async def held(*args, **kwargs):
        if state["first"]:  # the handoff's call, after it inserted the transfer row
            state["first"] = False
            entered.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(importance, "recompute_importance", held)

    handoff_task = asyncio.create_task(_assign(client, tenants, handoff_id, v1, bob))
    await asyncio.wait_for(entered.wait(), timeout=15)  # transfer written, uncommitted
    patch_task = asyncio.create_task(_patch(client, alice, v1, "Handoff first text, edited."))
    await _await_blocked_on_row_lock(db_engine)  # PATCH is provably waiting
    release.set()
    assigned, patched = await asyncio.wait_for(
        asyncio.gather(handoff_task, patch_task), timeout=30
    )

    assert assigned.status_code == 200, assigned.text
    assert patched.status_code == 409, patched.text
    assert patched.json()["error"]["code"] == "SM034", patched.text
    chain = await _chain_state(db_engine, tenants, v1)
    assert [(c[1], c[2]) for c in chain] == [(1, True)]  # no new version
    assert len(await _edits(db_engine, tenants, [v1])) == 1  # no new event
    transfers = [r for r in await _snapshot(db_engine, tenants, v1) if r[7] == "transfer"]
    assert len(transfers) == 1 and transfers[0][0] == str(bob)  # the committed transfer survives
    state_after = await _handoff_state(db_engine, tenants, handoff_id)
    assert state_after["assigned_count"] == 1
    assert state_after["assignments"] == [(str(v1), str(bob))]


# ── chronology of legacy chains, and never scoring a partial ancestry ────────


async def _state_for_ids(engine, data, memory_ids) -> dict[str, Any]:
    """Everything a refused request must leave untouched, for explicit memory ids
    (the recursive helper above would never terminate on a cyclic chain)."""
    ids = [str(m) for m in memory_ids]
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        memories = (
            await session.execute(
                text(
                    "SELECT id::text, version, current_version, parent_memory_id::text, content "
                    "FROM memories WHERE parent_memory_id = ANY(CAST(:ids AS uuid[])) "
                    "OR id = ANY(CAST(:ids AS uuid[])) ORDER BY version, id"
                ),
                {"ids": ids},
            )
        ).all()
        known = [m[0] for m in memories]
        edits = (
            await session.execute(
                text(
                    "SELECT id::text, memory_id::text, edit_position FROM attribution_edits "
                    "WHERE memory_id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
                ),
                {"ids": known},
            )
        ).all()
        attributions = (
            await session.execute(
                text(
                    "SELECT id::text FROM attributions "
                    "WHERE memory_id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
                ),
                {"ids": known},
            )
        ).all()
    return {
        "memories": [tuple(r) for r in memories],
        "edits": [tuple(r) for r in edits],
        "attributions": [tuple(r) for r in attributions],
    }


async def _insert_legacy_version(engine, data, parent_id, editor, before, after) -> uuid.UUID:
    """A version written the way the pre-fix code did: a child row whose single edit
    restarts at edit_position 1 and whose snapshot lists only that editor."""
    child = uuid.uuid4()
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        version = (
            await session.execute(
                text("SELECT version FROM memories WHERE id = CAST(:p AS uuid)"),
                {"p": str(parent_id)},
            )
        ).scalar_one()
        await session.execute(
            text("UPDATE memories SET current_version = FALSE WHERE id = CAST(:p AS uuid)"),
            {"p": str(parent_id)},
        )
        await session.execute(
            text(
                "INSERT INTO memories (id, workspace_id, parent_memory_id, content, content_hash, "
                "version, current_version) VALUES (CAST(:c AS uuid), CAST(:w AS uuid), "
                "CAST(:p AS uuid), :content, :h, :v, TRUE)"
            ),
            {
                "c": str(child),
                "w": str(data.target_workspace_id),
                "p": str(parent_id),
                "content": after,
                "h": uuid.uuid4().hex,
                "v": version + 1,
            },
        )
        edit_id = (
            await session.execute(
                text(
                    "INSERT INTO attribution_edits (memory_id, editor_id, content_before, "
                    "content_after, edit_position, action_type) VALUES (CAST(:m AS uuid), "
                    "CAST(:e AS uuid), :b, :a, 1, 'edit') RETURNING id"
                ),
                {"m": str(child), "e": str(editor), "b": before, "a": after},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO attributions (memory_id, user_id, contribution_weight, "
                "char_diff_score, semantic_score, temporal_score, structural_score, "
                "approval_score, trigger_action, edit_id) VALUES (CAST(:m AS uuid), "
                "CAST(:u AS uuid), 1.0, 0.5, 0.5, 1.0, 0.5, 0.0, 'edit', CAST(:e AS uuid))"
            ),
            {"m": str(child), "u": str(editor), "e": str(edit_id)},
        )
        await session.commit()
    return child


async def test_legacy_chain_with_restarted_positions_is_scored_chronologically(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Legacy first draft.")
    v2 = await _insert_legacy_version(
        db_engine, tenants, v1, bob, "Legacy first draft.", "Legacy first draft, Bob."
    )

    resp = await _patch(client, alice, v2, "Legacy first draft, Bob. Alice again.")

    assert resp.status_code == 200, resp.text
    v3 = uuid.UUID(resp.json()["data"]["id"])
    # The scorer gets consecutive chronological positions ...
    assert [(e.user_id, e.edit_position) for e in scorer.calls[-1]] == [
        (str(alice), 1),
        (str(bob), 2),
        (str(alice), 3),
    ]
    # ... while the stored positions are left exactly as they were written.
    stored = await _edits(db_engine, tenants, [v1, v2, v3])
    assert sorted((e[0], e[1]) for e in stored) == sorted(
        [(str(alice), 1), (str(bob), 1), (str(alice), 3)]
    )
    assert _contributors(await _get(client, alice, v3)) == {alice, bob}


async def _extend_chain(client, db_engine, tenants, root, users, count) -> list[uuid.UUID]:
    """PATCH ``count`` successive content edits, returning every version id."""
    ids = [root]
    for i in range(count):
        resp = await _patch(client, users[i % len(users)], ids[-1], f"Chain content {i + 1}.")
        assert resp.status_code == 200, resp.text
        ids.append(uuid.UUID(resp.json()["data"]["id"]))
    return ids


async def test_chain_at_the_bound_is_accepted_and_beyond_it_is_refused_with_zero_writes(
    client, db_engine, tenants, scorer, monkeypatch
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    monkeypatch.setattr(attribution_engine, "MAX_CHAIN_DEPTH", 2)
    v1 = await _seed_memory(db_engine, tenants, alice, "Bound text.")

    ids = await _extend_chain(client, db_engine, tenants, v1, (bob, alice), 2)  # v1 -> v2 -> v3
    assert len(ids) == 3  # the newest version has exactly two ancestors: at the bound
    before = await _state_for_ids(db_engine, tenants, ids)
    calls = len(scorer.calls)

    refused = await _patch(client, bob, ids[-1], "Past the bound.")

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "SM034", refused.text
    assert "ancestry_overflow" in refused.json()["error"]["message"], refused.text
    assert await _state_for_ids(db_engine, tenants, ids) == before  # no version, no event
    assert len(scorer.calls) == calls  # a partial history was never scored

    # Metadata-only updates do not read the ancestry and stay allowed at the bound.
    tagged = await _patch(client, bob, ids[-1], "Chain content 2.", tags=["still-ok"])
    assert tagged.status_code == 200, tagged.text


async def test_overflow_detected_after_the_version_exists_rolls_everything_back(
    client, db_engine, tenants, scorer, monkeypatch
) -> None:
    """Skip the preflight so the version row IS created and recompute then refuses:
    the failed request must leave no version, no flipped current_version flag, no
    event and no attribution row."""
    alice, bob = tenants.admin_id, tenants.member_id
    monkeypatch.setattr(attribution_engine, "MAX_CHAIN_DEPTH", 2)
    v1 = await _seed_memory(db_engine, tenants, alice, "Rollback text.")
    ids = await _extend_chain(client, db_engine, tenants, v1, (bob, alice), 2)
    before = await _state_for_ids(db_engine, tenants, ids)

    async def no_preflight(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        attribution_engine, "assert_edit_preserves_inherited_contributors", no_preflight
    )
    refused = await _patch(client, bob, ids[-1], "Created, then refused.")

    assert refused.status_code == 409, refused.text
    assert "ancestry_overflow" in refused.json()["error"]["message"], refused.text
    assert await _state_for_ids(db_engine, tenants, ids) == before


async def test_cycle_in_the_version_chain_is_refused_with_zero_writes(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Cycle text.")
    ids = await _extend_chain(client, db_engine, tenants, v1, (bob,), 1)  # v1 -> v2
    session = await _session(db_engine, tenants.owner_id, tenants.target_workspace_id)
    async with session:  # corrupt the chain: the root now claims the newest version as parent
        await session.execute(
            text(
                "UPDATE memories SET parent_memory_id = CAST(:p AS uuid) "
                "WHERE id = CAST(:c AS uuid)"
            ),
            {"p": str(ids[-1]), "c": str(ids[0])},
        )
        await session.commit()
    before = await _state_for_ids(db_engine, tenants, ids)
    calls = len(scorer.calls)

    refused = await _patch(client, alice, ids[-1], "Edit into a cycle.")

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "SM034", refused.text
    assert "ancestry_cycle" in refused.json()["error"]["message"], refused.text
    assert await _state_for_ids(db_engine, tenants, ids) == before
    assert len(scorer.calls) == calls


async def test_invalid_score_input_is_refused_by_the_route_and_the_request_rolls_back(
    client, db_engine, tenants, scorer, monkeypatch
) -> None:
    """D0: a non-finite raw score is refused inside the scorer's normalisation, after the new
    version row has been created. The request must answer the handled 409 SM034 and leave no
    new version, no flipped current_version flag, no event and no attribution row."""
    alice, bob = tenants.admin_id, tenants.member_id
    v1 = await _seed_memory(db_engine, tenants, alice, "Rollback on invalid scores.")
    before = await _write_state(db_engine, tenants, v1)

    class _NonFiniteScorer:
        """Real normalisation, fed a NaN raw score (as a corrupted signal would produce)."""

        def compute_scores(self, edits):
            return AttributionScorer()._normalize(
                [
                    ContributorScore(
                        user_id=edits[-1].user_id,
                        raw_score=float("nan"),
                        has_substantive_edit=True,
                    )
                ]
            )

    monkeypatch.setattr(attribution_engine, "get_scorer", lambda: _NonFiniteScorer())

    refused = await _patch(client, bob, v1, "Edited so that the scorer is asked to run.")

    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "SM034", refused.text
    assert "invalid_score_input" in refused.json()["error"]["message"], refused.text
    assert await _write_state(db_engine, tenants, v1) == before
