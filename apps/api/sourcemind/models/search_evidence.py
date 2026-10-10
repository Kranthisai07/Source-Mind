"""Append-only search and rating evidence for the bounded manual pilot."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from sourcemind.models.base import Base


class SearchEvent(Base):
    __tablename__ = "search_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id", name="uq_search_events_workspace_id"),
        CheckConstraint("btrim(query) <> ''", name="ck_search_events_query_not_empty"),
        CheckConstraint(
            "jsonb_typeof(search_parameters) = 'object'",
            name="ck_search_events_parameters_object",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'array'",
            name="ck_search_events_result_snapshot_array",
        ),
        CheckConstraint(
            "jsonb_typeof(memory_ids) = 'array'",
            name="ck_search_events_memory_ids_array",
        ),
        CheckConstraint(
            "jsonb_typeof(document_ids) = 'array'",
            name="ck_search_events_document_ids_array",
        ),
        Index("ix_search_events_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_search_events_requester_created", "requester_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    query: Mapped[str] = mapped_column(Text(), nullable=False)
    search_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB(), nullable=False)
    request_id: Mapped[str | None] = mapped_column(Text(), nullable=True)
    result_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(), nullable=False)
    memory_ids: Mapped[list[str]] = mapped_column(JSONB(), nullable=False)
    document_ids: Mapped[list[str | None]] = mapped_column(JSONB(), nullable=False)
    algorithm_id: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )


class SearchRating(Base):
    __tablename__ = "search_ratings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "search_event_id"],
            ["search_events.workspace_id", "search_events.id"],
            name="fk_search_ratings_event_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_search_ratings_workspace_idempotency",
        ),
        CheckConstraint("result_rank > 0", name="ck_search_ratings_rank_positive"),
        CheckConstraint(
            "rating_source IN ('independent', 'self', 'external')",
            name="ck_search_ratings_source",
        ),
        CheckConstraint(
            "allocation IS NULL OR (allocation >= 0 AND allocation <= 100)",
            name="ck_search_ratings_allocation_range",
        ),
        CheckConstraint(
            "(allocation IS NOT NULL) <> (exclusion_reason IS NOT NULL)",
            name="ck_search_ratings_value_or_exclusion",
        ),
        CheckConstraint(
            "(rating_source = 'external' AND rater_user_id IS NULL "
            "AND NULLIF(btrim(rater_pseudonym), '') IS NOT NULL) OR "
            "(rating_source IN ('independent', 'self') "
            "AND rater_user_id IS NOT NULL AND rater_pseudonym IS NULL)",
            name="ck_search_ratings_rater_identity",
        ),
        Index("ix_search_ratings_workspace_created", "workspace_id", "created_at", "id"),
        Index("ix_search_ratings_event_memory", "search_event_id", "memory_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default="gen_random_uuid()"
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    search_event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    memory_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("memories.id", ondelete="RESTRICT"), nullable=False
    )
    result_rank: Mapped[int] = mapped_column(Integer(), nullable=False)
    rater_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    rater_pseudonym: Mapped[str | None] = mapped_column(Text(), nullable=True)
    rating_source: Mapped[str] = mapped_column(String(20), nullable=False)
    allocation: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    exclusion_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    recorded_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="NOW()"
    )
