"""Ingestion state transitions tied to workspace access changes."""

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcemind.models.document import Document, IngestionStatus

REVOCATION_INGESTION_ERROR = "Workspace access revoked before ingestion."
DEPARTURE_INGESTION_ERROR = (
    "Workspace access ended before ingestion because the submitter began departure."
)


async def terminalize_member_ingestion_documents(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    error_message: str,
) -> int:
    """Fail queued or retrying documents before removing active membership."""
    documents_result = await session.execute(
        select(Document)
        .where(
            Document.workspace_id == workspace_id,
            Document.submitter_id == user_id,
            Document.deleted_at.is_(None),
            or_(
                Document.ingestion_status == IngestionStatus.PENDING.value,
                and_(
                    Document.ingestion_status == IngestionStatus.PROCESSING.value,
                    Document.pipeline_data["current_stage"].as_string() == "retrying",
                ),
            ),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    documents = documents_result.scalars().all()
    for document in documents:
        document.ingestion_status = IngestionStatus.FAILED.value
        document.error_message = error_message
        pipeline_data = dict(document.pipeline_data or {})
        pipeline_data["current_stage"] = "failed"
        document.pipeline_data = pipeline_data

    await session.flush()
    return len(documents)
