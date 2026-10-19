"""The version chain must not cross a workspace boundary (restricted runtime role, FORCE RLS).

``memories.parent_memory_id`` is a plain foreign key, so a row can legally point at a parent in
ANOTHER workspace (the foreign-key check ignores row-level security). Nothing in the application
creates such a link, but a runtime-role write path that did (or a bug) would make the history walk
cross the boundary. This test forges the link the only way it can exist and asks what each kind of
caller can read:

* a member of ONE workspace: row-level security hides the other workspace;
* a member of BOTH with no workspace context set (the combination the old query was exposed to):
  only the explicit workspace constraint in the query stops the walk.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.services.attribution.versioning import get_version_chain
from tests.integration.test_security_foundation_real_db import _seed_security_tenants

pytestmark = pytest.mark.asyncio


async def _memory(s: AsyncSession, ws: uuid.UUID, user: uuid.UUID, label: str, **kw) -> Memory:
    await set_rls_workspace_context(s, ws)
    doc = Document(
        workspace_id=ws, submitter_id=user, source_type="text",
        sha256_hash=hashlib.sha256((label + uuid.uuid4().hex).encode()).hexdigest(),
        ingestion_status="completed",
    )
    s.add(doc)
    await s.flush()
    mem = Memory(
        workspace_id=ws, document_id=doc.id, content=f"content of {label}",
        content_hash=hashlib.sha256((label + uuid.uuid4().hex).encode()).hexdigest(), **kw,
    )
    s.add(mem)
    await s.flush()
    return mem


async def test_history_never_crosses_the_workspace_boundary(supabase_engine: AsyncEngine) -> None:
    data = await _seed_security_tenants(supabase_engine)
    async with AsyncSession(supabase_engine, expire_on_commit=False) as s:
        await set_rls_user_context(s, data.owner_id)  # a member of both workspaces
        secret = await _memory(s, data.other_workspace_id, data.owner_id, "other-ws-secret")
        legit_old = await _memory(s, data.target_workspace_id, data.owner_id, "target-v1")
        head = await _memory(
            s, data.target_workspace_id, data.owner_id, "target-v2",
            parent_memory_id=legit_old.id, version=2,
        )
        # The forged link: the oldest target version points at the other workspace's memory.
        await s.execute(
            text("UPDATE memories SET parent_memory_id = :p WHERE id = :i"),
            {"p": secret.id, "i": legit_old.id},
        )
        deleted = await _memory(s, data.target_workspace_id, data.owner_id, "target-deleted-ancestor")
        await s.execute(
            text("UPDATE memories SET deleted_at = now() WHERE id = :i"), {"i": deleted.id}
        )
        await s.execute(
            text("UPDATE memories SET parent_memory_id = :p WHERE id = :i"),
            {"p": deleted.id, "i": secret.id},
        )
        await s.commit()
        head_id, secret_id, legit_id, deleted_id = head.id, secret.id, legit_old.id, deleted.id

    def ids(chain: list[dict]) -> set[str]:
        return {c["id"] for c in chain}

    # 1. A member of both workspaces, NO workspace context set: only the query's own constraint
    #    can keep the other workspace out.
    async with AsyncSession(supabase_engine) as s:
        await set_rls_user_context(s, data.owner_id)
        chain = await get_version_chain(s, head_id, data.target_workspace_id)
        assert ids(chain) == {str(head_id), str(legit_id)}, chain
        assert all("other-ws-secret" not in c["content"] for c in chain)
        # Asking for the head under the WRONG workspace yields nothing at all.
        assert await get_version_chain(s, head_id, data.other_workspace_id) == []

    # 2. Pre-fix behaviour, to prove the test has teeth: the unconstrained walk does leak here.
    async with AsyncSession(supabase_engine) as s:
        await set_rls_user_context(s, data.owner_id)
        leaked = (
            await s.execute(
                text(
                    "WITH RECURSIVE c AS (SELECT id, content, parent_memory_id FROM memories "
                    "WHERE id = :h UNION ALL SELECT m.id, m.content, m.parent_memory_id "
                    "FROM memories m JOIN c ON m.id = c.parent_memory_id) SELECT id FROM c"
                ),
                {"h": head_id},
            )
        ).scalars().all()
        assert secret_id in leaked, "expected the unconstrained query to cross the boundary"

    # 3. A member of ONE workspace (row-level security) and a soft-deleted ancestor: never shown.
    async with AsyncSession(supabase_engine) as s:
        await set_rls_user_context(s, data.member_id)
        await set_rls_workspace_context(s, data.target_workspace_id)
        chain = await get_version_chain(s, head_id, data.target_workspace_id)
        assert ids(chain) == {str(head_id), str(legit_id)}
        assert str(deleted_id) not in ids(chain)
