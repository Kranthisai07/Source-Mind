"""Real PostgreSQL workspace-isolation regression for the pilot export."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.models.attribution import Attribution
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.models.workspace import Workspace, WorkspaceMember
from sourcemind.services.pilot_export import export_workspace_pilot_data


async def _seed_export_rows(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    label: str,
) -> tuple[Document, Memory, Attribution]:
    content = f"{label} pilot memory"
    document = Document(
        workspace_id=workspace_id,
        submitter_id=user_id,
        title=f"{label} document",
        source_type="text",
        sha256_hash=hashlib.sha256(f"{label}-document".encode()).hexdigest(),
        ingestion_status="completed",
        memory_count=1,
    )
    session.add(document)
    await session.flush()

    memory = Memory(
        workspace_id=workspace_id,
        document_id=document.id,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )
    session.add(memory)
    await session.flush()

    attribution = Attribution(
        memory_id=memory.id,
        user_id=user_id,
        contribution_weight=1.0,
        trigger_action="create",
    )
    session.add(attribution)
    await session.flush()
    return document, memory, attribution


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pilot_export_excludes_other_workspace_rows_real_postgres(
    db_session: AsyncSession,
    test_org,
    test_user,
    test_workspace,
) -> None:
    other_workspace_id = uuid.uuid4()
    await set_rls_user_context(db_session, test_user.id)
    await set_rls_workspace_context(db_session, other_workspace_id)
    other_workspace = Workspace(
        id=other_workspace_id,
        organization_id=test_org.id,
        created_by_user_id=test_user.id,
        name="Other Pilot Workspace",
        slug=f"other-pilot-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(other_workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=other_workspace_id,
            user_id=test_user.id,
            role="owner",
        )
    )
    await db_session.flush()

    await set_rls_workspace_context(db_session, test_workspace.id)
    document_a, memory_a, attribution_a = await _seed_export_rows(
        db_session,
        test_workspace.id,
        test_user.id,
        "workspace-a",
    )
    await set_rls_workspace_context(db_session, other_workspace_id)
    document_b, memory_b, attribution_b = await _seed_export_rows(
        db_session,
        other_workspace_id,
        test_user.id,
        "workspace-b",
    )

    await set_rls_workspace_context(db_session, test_workspace.id)
    exported = await export_workspace_pilot_data(db_session, test_workspace.id)

    assert {row["id"] for row in exported["documents"]} == {str(document_a.id)}
    assert {row["id"] for row in exported["memories"]} == {str(memory_a.id)}
    assert {row["id"] for row in exported["versions"]} == {str(memory_a.id)}
    assert {row["id"] for row in exported["attributions"]} == {
        str(attribution_a.id)
    }
    exported_ids = {
        row["id"]
        for dataset in ("documents", "memories", "versions", "attributions")
        for row in exported[dataset]
    }
    assert exported_ids.isdisjoint(
        {str(document_b.id), str(memory_b.id), str(attribution_b.id)}
    )
