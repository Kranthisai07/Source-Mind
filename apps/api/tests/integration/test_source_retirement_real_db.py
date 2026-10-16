# ruff: noqa: F811, E501
"""Source retirement vs. human edits against real PostgreSQL (contract C9, C10).

Every race uses independent database sessions. Deterministic orderings (T-K1, T-K2)
hold one transaction open and prove the other is blocked on a row lock before
releasing it; the free races (T-K3/K4/K5) are repeated and must always end in ONE
consistent outcome per memory with no deadlock (asyncio.wait_for bounds every run).

SIMULATED: no GitHub, no providers; the 5-signal scorer is the recording equal-split
fake from the attribution history tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text

from sourcemind.models.connector import ArtifactLink
from sourcemind.models.document import Document
from sourcemind.services.attribution import engine as attribution_engine
from sourcemind.services.memory.retire import STALE_REASON_FOR, retire_document_memories
from tests.integration.test_attribution_history_real_db import (  # noqa: F401
    _await_blocked_on_row_lock,
    _patch,
    _seed_memory,
    _session,
    _snapshot,
    client,
    db_engine,
    scorer,
    tenants,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


# ── helpers ──────────────────────────────────────────────────────────────────


async def _seed_source(engine, data, source_id: str, seq: int, *, url: str | None = None):
    """A Document plus the ArtifactLink of revision ``seq`` of ``source_id``."""
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        doc = Document(
            workspace_id=data.target_workspace_id,
            submitter_id=data.owner_id,
            source_type="text",
            sha256_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            ingestion_status="completed",
        )
        session.add(doc)
        await session.flush()
        session.add(
            ArtifactLink(
                workspace_id=data.target_workspace_id,
                document_id=doc.id,
                source_tool="github",
                source_type="pull_request",
                source_id=source_id,
                source_url=url or f"https://github.test/{source_id}#r{seq}",
                source_author="octocat",
                source_timestamp=datetime(2026, 10, 1, tzinfo=UTC),
                source_revision=f"rev-{seq}",
                revision_seq=seq,
            )
        )
        await session.commit()
        return doc.id


async def _retire(engine, data, doc_id, reason="source_superseded", *, entered=None, hold=None):
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        outcome = await retire_document_memories(
            session, workspace_id=data.target_workspace_id, document_id=doc_id, reason=reason
        )
        if entered is not None:
            entered.set()
            await hold.wait()
        await session.commit()
        return outcome


async def _row(engine, data, memory_id) -> dict:
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        r = (
            await session.execute(
                text(
                    "SELECT id, deleted_at, deleted_reason, source_stale_at, "
                    "source_stale_reason, current_version, version FROM memories "
                    "WHERE id = CAST(:m AS uuid)"
                ),
                {"m": str(memory_id)},
            )
        ).mappings().one()
        return dict(r)


async def _chain_ids(engine, data, root_id) -> list[uuid.UUID]:
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        rows = await session.execute(
            text(
                "WITH RECURSIVE c AS (SELECT id FROM memories WHERE id = CAST(:r AS uuid) "
                "UNION ALL SELECT m.id FROM memories m JOIN c ON m.parent_memory_id = c.id) "
                "SELECT id FROM c"
            ),
            {"r": str(root_id)},
        )
        return [r[0] for r in rows]


async def _head(engine, data, root_id) -> dict:
    ids = await _chain_ids(engine, data, root_id)
    rows = [await _row(engine, data, i) for i in ids]
    return next(r for r in rows if r["current_version"])


async def _edit_count(engine, data) -> int:
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        return (
            await session.execute(
                text(
                    "SELECT count(*) FROM attribution_edits ae JOIN memories m "
                    "ON m.id = ae.memory_id WHERE m.workspace_id = CAST(:w AS uuid)"
                ),
                {"w": str(data.target_workspace_id)},
            )
        ).scalar_one()


async def _get(client, user, memory_id) -> httpx.Response:
    return await client.get(f"/v1/memories/{memory_id}", headers={"X-Test-User": str(user)})


async def _reapply(client, user, retired_id, target, content, *, base_version=None, key=None):
    body = {"target_memory_id": str(target), "content": content}
    if base_version is not None:
        body["base_version"] = base_version
    return await client.post(
        f"/v1/memories/{retired_id}/reapply",
        json=body,
        headers={"X-Test-User": str(user), "Idempotency-Key": key or str(uuid.uuid4())},
    )


def _code(resp: httpx.Response) -> str | None:
    try:
        return resp.json()["error"]["code"]
    except Exception:
        return None


# ── T-H: human-edited lineages survive refresh ───────────────────────────────


async def test_h1_h2_h3_edited_lineage_flagged_unedited_sibling_retired(
    client, db_engine, tenants, scorer
) -> None:
    alice, bob = tenants.admin_id, tenants.member_id
    doc = await _seed_source(db_engine, tenants, f"o/r/pull/{uuid.uuid4()}", 1)
    edited = await _seed_memory(db_engine, tenants, tenants.owner_id, "Edited fact.", document_id=doc)
    sibling = await _seed_memory(db_engine, tenants, tenants.owner_id, "Sibling.", document_id=doc)
    r = await _patch(client, alice, edited, "Edited fact, improved by Alice.")
    assert r.status_code == 200, r.text
    head_id = uuid.UUID(r.json()["data"]["id"])

    ids = [edited, head_id, sibling]
    before = {i: await _snapshot(db_engine, tenants, i) for i in ids}
    edits_before = await _edit_count(db_engine, tenants)

    out = await _retire(db_engine, tenants, doc)

    assert out.flagged_ids == [head_id] and out.retired_ids == [sibling]  # T-H1, T-H2
    head = await _row(db_engine, tenants, head_id)
    assert head["deleted_at"] is None and head["source_stale_reason"] == "source_revised"
    assert head["source_stale_at"] is not None
    old = await _row(db_engine, tenants, edited)
    assert old["deleted_at"] is None and old["source_stale_at"] is None  # non-head untouched
    sib = await _row(db_engine, tenants, sibling)
    assert sib["deleted_at"] is not None and sib["deleted_reason"] == "source_superseded"
    # still readable / searchable path (deleted_at IS NULL filter); machine-only hidden
    assert (await _get(client, bob, head_id)).status_code == 200
    assert (await _get(client, bob, sibling)).status_code == 404
    # T-H3: attribution rows and edit events unchanged
    for i in ids:
        assert await _snapshot(db_engine, tenants, i) == before[i]
    assert await _edit_count(db_engine, tenants) == edits_before


async def test_retirement_is_idempotent(db_engine, tenants, scorer) -> None:
    doc = await _seed_source(db_engine, tenants, f"o/r/pull/{uuid.uuid4()}", 1)
    only = await _seed_memory(db_engine, tenants, tenants.owner_id, "Only.", document_id=doc)
    first = await _retire(db_engine, tenants, doc)
    row1 = await _row(db_engine, tenants, only)
    second = await _retire(db_engine, tenants, doc)
    assert first.retired_ids == [only]
    assert second.retired_ids == [] and second.flagged_ids == []
    assert await _row(db_engine, tenants, only) == row1


@pytest.mark.parametrize("reason", sorted(STALE_REASON_FOR))
async def test_h4_deletion_and_removal_respect_the_rule(
    client, db_engine, tenants, scorer, reason
) -> None:
    doc = await _seed_source(db_engine, tenants, f"o/r/pull/{uuid.uuid4()}", 1)
    machine = await _seed_memory(db_engine, tenants, tenants.owner_id, "Machine.", document_id=doc)
    human = await _seed_memory(db_engine, tenants, tenants.owner_id, "Human.", document_id=doc)
    r = await _patch(client, tenants.admin_id, human, "Human, edited.")
    head_id = uuid.UUID(r.json()["data"]["id"])

    out = await _retire(db_engine, tenants, doc, reason)
    assert out.retired_ids == [machine] and out.flagged_ids == [head_id]
    assert (await _row(db_engine, tenants, machine))["deleted_reason"] == reason
    head = await _row(db_engine, tenants, head_id)
    assert head["source_stale_reason"] == STALE_REASON_FOR[reason]
    assert head["deleted_at"] is None


# ── T-K1 / T-K2: deterministic orderings ─────────────────────────────────────


async def test_k1_patch_commits_first_then_retirement_only_flags(
    client, db_engine, tenants, scorer, monkeypatch
) -> None:
    doc = await _seed_source(db_engine, tenants, f"o/r/pull/{uuid.uuid4()}", 1)
    mem = await _seed_memory(db_engine, tenants, tenants.owner_id, "Fact K1.", document_id=doc)
    entered, release = asyncio.Event(), asyncio.Event()
    original = attribution_engine.recompute_attribution

    async def held(*a, **k):
        entered.set()
        await release.wait()
        return await original(*a, **k)

    monkeypatch.setattr(attribution_engine, "recompute_attribution", held)
    patch_task = asyncio.create_task(_patch(client, tenants.admin_id, mem, "Fact K1 edited."))
    await asyncio.wait_for(entered.wait(), timeout=15)
    retire_task = asyncio.create_task(_retire(db_engine, tenants, doc))
    await _await_blocked_on_row_lock(db_engine)
    release.set()
    resp, out = await asyncio.wait_for(asyncio.gather(patch_task, retire_task), timeout=30)

    assert resp.status_code == 200, resp.text
    new_id = uuid.UUID(resp.json()["data"]["id"])
    assert out.retired_ids == [] and out.flagged_ids == [new_id]
    assert (await _row(db_engine, tenants, mem))["deleted_at"] is None
    assert (await _row(db_engine, tenants, new_id))["source_stale_reason"] == "source_revised"


async def test_k2_retirement_commits_first_then_patch_409_with_candidates(
    client, db_engine, tenants, scorer
) -> None:
    sid = f"o/r/pull/{uuid.uuid4()}"
    doc1 = await _seed_source(db_engine, tenants, sid, 1)
    doc2 = await _seed_source(db_engine, tenants, sid, 2, url="https://github.test/new")
    mem = await _seed_memory(db_engine, tenants, tenants.owner_id, "Fact K2.", document_id=doc1)
    cand = await _seed_memory(db_engine, tenants, tenants.owner_id, "Fact K2 v2.", document_id=doc2)
    edits_before = await _edit_count(db_engine, tenants)

    entered, hold = asyncio.Event(), asyncio.Event()
    retire_task = asyncio.create_task(
        _retire(db_engine, tenants, doc1, entered=entered, hold=hold)
    )
    await asyncio.wait_for(entered.wait(), timeout=15)
    patch_task = asyncio.create_task(_patch(client, tenants.admin_id, mem, "My draft K2."))
    await _await_blocked_on_row_lock(db_engine)
    hold.set()
    resp, _ = await asyncio.wait_for(asyncio.gather(patch_task, retire_task), timeout=30)

    assert resp.status_code == 409 and _code(resp) == "source_retired", resp.text
    d = resp.json()["error"]["details"]
    assert d["reason"] == "source_superseded"
    assert d["newer_source_url"] == "https://github.test/new"
    assert d["candidates"] == [{"memory_id": str(cand), "preview": "Fact K2 v2."}]
    assert len(await _chain_ids(db_engine, tenants, mem)) == 1  # nothing created
    assert await _edit_count(db_engine, tenants) == edits_before


# ── T-K3 / T-K4 / T-K5: free races, consistent outcome every time ────────────


async def _assert_consistent(engine, data, root, resp, reason) -> str:
    if resp.status_code == 200:
        chain = await _chain_ids(engine, data, root)
        assert len(chain) == 2, "lost update or fork"
        head = await _head(engine, data, root)
        assert head["deleted_at"] is None
        assert head["source_stale_reason"] == STALE_REASON_FOR[reason]
        assert (await _row(engine, data, root))["deleted_at"] is None
        return "edited"
    assert resp.status_code == 409 and _code(resp) == "source_retired", resp.text
    assert resp.json()["error"]["details"]["reason"] == reason
    assert len(await _chain_ids(engine, data, root)) == 1
    row = await _row(engine, data, root)
    assert row["deleted_reason"] == reason and row["current_version"]
    return "retired"


@pytest.mark.parametrize(
    ("reason", "repeats"),
    [("source_superseded", 50), ("connector_removed", 20)],
    ids=["k3-supersede", "k5-connector-removal"],
)
async def test_k3_k5_patch_races_retirement(
    client, db_engine, tenants, scorer, reason, repeats
) -> None:
    seen: set[str] = set()
    for _ in range(repeats):
        doc = await _seed_source(db_engine, tenants, f"o/r/pull/{uuid.uuid4()}", 1)
        mem = await _seed_memory(db_engine, tenants, tenants.owner_id, "Race.", document_id=doc)
        jitter = random.random() * 0.03

        async def late_retire(doc=doc, jitter=jitter):
            await asyncio.sleep(jitter)
            return await _retire(db_engine, tenants, doc, reason)

        resp, out = await asyncio.wait_for(
            asyncio.gather(_patch(client, tenants.admin_id, mem, "Race, edited."), late_retire()),
            timeout=30,
        )
        seen.add(await _assert_consistent(db_engine, tenants, mem, resp, reason))
        if resp.status_code == 200:
            assert out.retired_ids == [] and len(out.flagged_ids) == 1
        else:
            assert out.retired_ids == [mem]
    assert seen  # outcome mix is timing dependent; every single outcome was consistent


async def test_k4_retirement_races_two_patches_on_siblings(
    client, db_engine, tenants, scorer
) -> None:
    for _ in range(20):
        doc = await _seed_source(db_engine, tenants, f"o/r/pull/{uuid.uuid4()}", 1)
        a = await _seed_memory(db_engine, tenants, tenants.owner_id, "Sib A.", document_id=doc)
        b = await _seed_memory(db_engine, tenants, tenants.owner_id, "Sib B.", document_id=doc)
        jitter = random.random() * 0.03

        async def late_retire(doc=doc, jitter=jitter):
            await asyncio.sleep(jitter)
            return await _retire(db_engine, tenants, doc)

        ra, rb, _ = await asyncio.wait_for(
            asyncio.gather(
                _patch(client, tenants.admin_id, a, "Sib A edited."),
                _patch(client, tenants.member_id, b, "Sib B edited."),
                late_retire(),
            ),
            timeout=40,
        )
        await _assert_consistent(db_engine, tenants, a, ra, "source_superseded")
        await _assert_consistent(db_engine, tenants, b, rb, "source_superseded")


# ── T-K6..K9: re-apply ───────────────────────────────────────────────────────


async def _retired_with_candidates(engine, data, n_candidates: int):
    sid = f"o/r/pull/{uuid.uuid4()}"
    doc1 = await _seed_source(engine, data, sid, 1)
    retired = await _seed_memory(engine, data, data.owner_id, "Old fact.", document_id=doc1)
    cands: list[uuid.UUID] = []
    if n_candidates:
        doc2 = await _seed_source(engine, data, sid, 2)
        for i in range(n_candidates):
            cands.append(
                await _seed_memory(engine, data, data.owner_id, f"New fact {i}.", document_id=doc2)
            )
    out = await _retire(engine, data, doc1)
    assert out.retired_ids == [retired]
    return retired, cands


async def test_k6_three_candidates_no_valid_target_is_rejected_no_autopick(
    client, db_engine, tenants, scorer
) -> None:
    retired, cands = await _retired_with_candidates(db_engine, tenants, 3)
    edits_before = await _edit_count(db_engine, tenants)
    patch = await _patch(client, tenants.admin_id, retired, "Draft.")
    assert patch.status_code == 409 and _code(patch) == "source_retired"
    assert {c["memory_id"] for c in patch.json()["error"]["details"]["candidates"]} == {
        str(c) for c in cands
    }
    # missing target -> request validation; a random target -> rejected; nothing chosen
    missing = await client.post(
        f"/v1/memories/{retired}/reapply",
        json={"content": "Draft."},
        headers={"X-Test-User": str(tenants.admin_id), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert missing.status_code == 422
    bad = await _reapply(client, tenants.admin_id, retired, uuid.uuid4(), "Draft.")
    assert bad.status_code == 422
    assert await _edit_count(db_engine, tenants) == edits_before
    for c in cands:
        assert (await _row(db_engine, tenants, c))["version"] == 1


async def test_k7_zero_candidates_no_replacement_creates_nothing(
    client, db_engine, tenants, scorer
) -> None:
    retired, _ = await _retired_with_candidates(db_engine, tenants, 0)
    edits_before = await _edit_count(db_engine, tenants)
    patch = await _patch(client, tenants.admin_id, retired, "Draft.")
    assert patch.status_code == 409 and _code(patch) == "source_retired"
    assert patch.json()["error"]["details"]["candidates"] == []
    resp = await _reapply(client, tenants.admin_id, retired, uuid.uuid4(), "Draft.")
    assert resp.status_code == 409 and _code(resp) == "no_replacement"
    assert await _edit_count(db_engine, tenants) == edits_before
    assert len(await _chain_ids(db_engine, tenants, retired)) == 1


async def test_k8_invalid_targets_rejected_without_side_effects(
    client, db_engine, tenants, scorer
) -> None:
    retired, cands = await _retired_with_candidates(db_engine, tenants, 2)
    other_ws = await _seed_memory(
        db_engine, tenants, tenants.other_workspace_member_id, "Other ws.",
        workspace_id=tenants.other_workspace_id,
    )
    unrelated = await _seed_memory(db_engine, tenants, tenants.owner_id, "Unrelated.")
    # a candidate that is itself retired afterwards is no longer a candidate
    gone = await _seed_memory(
        db_engine, tenants, tenants.owner_id, "Gone.",
        document_id=(await _doc_of(db_engine, tenants, cands[0])),
    )
    await _retire_one(db_engine, tenants, gone)
    edits_before = await _edit_count(db_engine, tenants)

    for bad in (other_ws, unrelated, gone, retired):
        resp = await _reapply(client, tenants.admin_id, retired, bad, "Draft.")
        assert resp.status_code in (404, 422), (bad, resp.text)
    # a plain current memory cannot be the SOURCE of a re-apply either
    resp = await _reapply(client, tenants.admin_id, unrelated, cands[0], "Draft.")
    assert resp.status_code == 422
    # a viewer cannot re-apply
    resp = await _reapply(client, tenants.viewer_id, retired, cands[0], "Draft.")
    assert resp.status_code in (403, 404)
    assert await _edit_count(db_engine, tenants) == edits_before
    for c in cands:
        assert (await _row(db_engine, tenants, c))["version"] == 1


async def _doc_of(engine, data, memory_id) -> uuid.UUID:
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        return (
            await session.execute(
                text("SELECT document_id FROM memories WHERE id = CAST(:m AS uuid)"),
                {"m": str(memory_id)},
            )
        ).scalar_one()


async def _retire_one(engine, data, memory_id) -> None:
    session = await _session(engine, data.owner_id, data.target_workspace_id)
    async with session:
        await session.execute(
            text(
                "UPDATE memories SET deleted_at = NOW(), deleted_reason = 'source_superseded' "
                "WHERE id = CAST(:m AS uuid)"
            ),
            {"m": str(memory_id)},
        )
        await session.commit()


async def test_k9_valid_target_records_edit_under_acting_editor(
    client, db_engine, tenants, scorer
) -> None:
    retired, cands = await _retired_with_candidates(db_engine, tenants, 3)
    target = cands[1]
    owner, bob = tenants.owner_id, tenants.member_id
    target_before = await _snapshot(db_engine, tenants, target)
    others_before = {c: await _snapshot(db_engine, tenants, c) for c in cands if c != target}

    stale = await _reapply(client, bob, retired, target, "Draft.", base_version=7)
    assert stale.status_code == 409 and _code(stale) == "SM035"

    resp = await _reapply(client, bob, retired, target, "New fact 1, with Bob's draft.", base_version=1)
    assert resp.status_code == 200, resp.text
    new_id = uuid.UUID(resp.json()["data"]["id"])
    assert resp.json()["data"]["version"] == 2

    session = await _session(db_engine, owner, tenants.target_workspace_id)
    async with session:
        edits = (
            await session.execute(
                text(
                    "SELECT editor_id::text, action_type, edit_position FROM attribution_edits "
                    "WHERE memory_id = ANY(CAST(:ids AS uuid[])) ORDER BY edit_position"
                ),
                {"ids": [str(target), str(new_id)]},
            )
        ).all()
    # existing creation history kept, the re-apply is a new 'edit' under the acting editor
    assert [(e[0], e[1]) for e in edits] == [(str(owner), "create"), (str(bob), "edit")]
    # the target's own contribution snapshot is untouched and the new head carries both
    assert await _snapshot(db_engine, tenants, target) == target_before
    holders = {row[0] for row in await _snapshot(db_engine, tenants, new_id)}
    assert holders == {str(owner), str(bob)}
    for c, snap in others_before.items():  # other candidates and the retired memory untouched
        assert await _snapshot(db_engine, tenants, c) == snap
    assert (await _row(db_engine, tenants, retired))["deleted_reason"] == "source_superseded"
    assert len(await _chain_ids(db_engine, tenants, retired)) == 1
