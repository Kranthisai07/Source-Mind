"""Real PostgreSQL workspace-isolation regression for the pilot export."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.core.database import set_rls_user_context, set_rls_workspace_context
from sourcemind.models.attribution import Attribution, AttributionEdit
from sourcemind.models.document import Document
from sourcemind.models.memory import Memory
from sourcemind.models.memory_conflict import MemoryConflict
from sourcemind.models.memory_relation import MemoryRelation
from sourcemind.models.search_evidence import SearchEvent, SearchRating
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


async def _seed_export_audit_rows(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    document: Document,
    memory: Memory,
    label: str,
) -> dict[str, uuid.UUID]:
    companion_content = f"{label} companion memory"
    companion = Memory(
        workspace_id=workspace_id,
        document_id=document.id,
        content=companion_content,
        content_hash=hashlib.sha256(companion_content.encode()).hexdigest(),
    )
    session.add(companion)
    await session.flush()

    edit = AttributionEdit(
        memory_id=memory.id,
        editor_id=user_id,
        content_before=None,
        content_after=memory.content,
        edit_position=1,
        action_type="create",
        idempotency_key=str(uuid.uuid4()),
    )
    relation = MemoryRelation(
        source_memory_id=memory.id,
        target_memory_id=companion.id,
        relation_type="extends",
        similarity_score=0.75,
        confidence=0.9,
        detected_by="user",
    )
    conflict = MemoryConflict(
        workspace_id=workspace_id,
        memory_a_id=memory.id,
        memory_b_id=companion.id,
        conflict_type="ambiguity",
        similarity_score=0.8,
    )
    session.add_all([edit, relation, conflict])
    await session.flush()

    event = SearchEvent(
        workspace_id=workspace_id,
        requester_user_id=user_id,
        query=f"{label} query",
        search_parameters={"query": f"{label} query", "mode": "hybrid"},
        request_id=f"{label}-request",
        result_snapshot=[
            {
                "memory": {"id": str(memory.id), "document_id": str(document.id)},
                "score": 0.7,
                "rank": 1,
            }
        ],
        memory_ids=[str(memory.id)],
        document_ids=[str(document.id)],
        algorithm_id="memory-search-v1:hybrid",
    )
    session.add(event)
    await session.flush()
    rating = SearchRating(
        workspace_id=workspace_id,
        search_event_id=event.id,
        memory_id=memory.id,
        result_rank=1,
        rater_pseudonym=f"{label}-external",
        rating_source="external",
        allocation=40,
        idempotency_key=uuid.uuid4(),
        recorded_by_user_id=user_id,
    )
    session.add(rating)
    await session.flush()
    return {
        "companion": companion.id,
        "edit": edit.id,
        "relation": relation.id,
        "conflict": conflict.id,
        "event": event.id,
        "rating": rating.id,
    }


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
    audit_a = await _seed_export_audit_rows(
        db_session,
        workspace_id=test_workspace.id,
        user_id=test_user.id,
        document=document_a,
        memory=memory_a,
        label="workspace-a",
    )
    await set_rls_workspace_context(db_session, other_workspace_id)
    document_b, memory_b, attribution_b = await _seed_export_rows(
        db_session,
        other_workspace_id,
        test_user.id,
        "workspace-b",
    )
    audit_b = await _seed_export_audit_rows(
        db_session,
        workspace_id=other_workspace_id,
        user_id=test_user.id,
        document=document_b,
        memory=memory_b,
        label="workspace-b",
    )

    await set_rls_workspace_context(db_session, test_workspace.id)
    exported = await export_workspace_pilot_data(db_session, test_workspace.id)

    assert {row["id"] for row in exported["documents"]} == {str(document_a.id)}
    assert {row["id"] for row in exported["memories"]} == {
        str(memory_a.id),
        str(audit_a["companion"]),
    }
    assert {row["id"] for row in exported["versions"]} == {
        str(memory_a.id),
        str(audit_a["companion"]),
    }
    assert {row["id"] for row in exported["attributions"]} == {
        str(attribution_a.id)
    }
    assert {row["id"] for row in exported["attribution_edits"]} == {
        str(audit_a["edit"])
    }
    assert {row["id"] for row in exported["relations"]} == {
        str(audit_a["relation"])
    }
    assert {row["id"] for row in exported["conflicts"]} == {
        str(audit_a["conflict"])
    }
    assert {row["id"] for row in exported["search_events"]} == {
        str(audit_a["event"])
    }
    assert {row["id"] for row in exported["search_ratings"]} == {
        str(audit_a["rating"])
    }
    assert {row["workspace_id"] for row in exported["workspace_members"]} == {
        str(test_workspace.id)
    }
    assert exported["canonical_hash_algorithm"] == "sha256"
    assert len(exported["canonical_hash"]) == 64
    assert exported["unavailable_datasets"] == {}
    exported_ids = {
        row["id"]
        for dataset in (
            "documents",
            "memories",
            "versions",
            "attributions",
            "attribution_edits",
            "relations",
            "conflicts",
            "search_events",
            "search_ratings",
        )
        for row in exported[dataset]
    }
    assert exported_ids.isdisjoint(
        {
            str(document_b.id),
            str(memory_b.id),
            str(attribution_b.id),
            *(str(value) for value in audit_b.values()),
        }
    )
