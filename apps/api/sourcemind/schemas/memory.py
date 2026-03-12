"""
Pydantic schemas for Memory resources and the ingestion pipeline.

Includes:
  - MemoryCreate / MemoryUpdate: mutation inputs
  - MemoryResponse: full memory representation with attribution
  - IngestionJobResponse: async job status polling
  - SearchRequest / SearchResponse: hybrid semantic search
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from sourcemind.models.document import DocumentSourceType
from sourcemind.schemas.attribution import AttributionResponse, ContributionBreakdown


class SearchMode(StrEnum):
    """Search algorithm selection."""

    SEMANTIC = "semantic"    # pgvector cosine similarity only
    KEYWORD = "keyword"      # PostgreSQL BM25 only
    HYBRID = "hybrid"        # Reciprocal Rank Fusion (default)


# ─── Ingestion (Stage 1: RECEIVE) ────────────────────────────────────────────

class MemoryCreate(BaseModel):
    """
    Input for POST /v1/memories — ingest new content.

    One of (content, url) must be provided.
    The ingestion is asynchronous — response is 202 Accepted with a job_id.
    Poll GET /v1/memories/jobs/:id for status.
    """

    # Content input — provide exactly one
    content: str | None = Field(
        default=None,
        max_length=500_000,  # ~100k tokens
        description="Raw text content to ingest",
    )
    url: str | None = Field(
        default=None,
        max_length=2048,
        description="URL to fetch and ingest (Playwright + readability)",
    )
    source_type: DocumentSourceType = Field(
        default=DocumentSourceType.TEXT,
        description="Content type hint for the extraction stage",
    )

    # Optional metadata
    title: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Tags to apply to all extracted memories",
    )

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def require_content_or_url(self) -> "MemoryCreate":
        if not self.content and not self.url:
            raise ValueError("Either 'content' or 'url' must be provided.")
        if self.content and self.url:
            raise ValueError("Provide either 'content' or 'url', not both.")
        return self


class MemoryUpdate(BaseModel):
    """
    Input for PATCH /v1/memories/:id — update memory content.

    Triggers attribution recompute (5-signal engine) and creates a new version.
    Requires Idempotency-Key header.
    """

    content: str = Field(
        min_length=1,
        max_length=500_000,
        description="Updated memory content",
    )
    tags: list[str] | None = Field(
        default=None,
        description="If provided, replaces the existing tags",
    )

    model_config = {"str_strip_whitespace": True}


# ─── Response schemas ─────────────────────────────────────────────────────────

class IngestionJobResponse(BaseModel):
    """
    Response for POST /v1/memories (202 Accepted) and
    GET /v1/memories/jobs/:id (status polling).
    """

    job_id: str = Field(description="Celery task ID. Use with GET /v1/memories/jobs/:id")
    document_id: UUID = Field(description="Document record created for this ingestion")
    status: str = Field(description="IngestionStatus enum value")
    message: str = Field(description="Human-readable status message")
    # Populated once ingestion is complete
    memory_ids: list[UUID] = Field(
        default_factory=list,
        description="IDs of memories extracted from this document",
    )
    memory_count: int | None = Field(
        default=None, description="Number of memories extracted"
    )
    chunk_count: int | None = Field(
        default=None, description="Number of chunks produced"
    )
    error: str | None = Field(
        default=None, description="Error message if status=failed"
    )


class MemoryVersionResponse(BaseModel):
    """A single version entry in a memory's version history."""

    id: UUID
    version: int
    content: str
    created_at: datetime
    editor: "UserSummaryInline"
    is_current: bool

    model_config = {"from_attributes": True}


class MemoryResponse(BaseModel):
    """
    Full memory representation.

    Detail level varies by role (per API contract):
      owner/admin/member → full content + full attribution breakdown
      viewer → summary only (content truncated, no individual attribution weights)
    """

    id: UUID
    workspace_id: UUID
    document_id: UUID | None
    content: str
    version: int
    tags: list[str] | None
    category: str | None
    confidence_score: float | None
    created_at: datetime
    updated_at: datetime | None

    # Attribution breakdown — populated when include_attribution=true
    attribution: list[ContributionBreakdown] | None = Field(
        default=None,
        description="Contributor breakdown. Null unless include_attribution=true.",
    )
    # Relationship summary
    relation_count: int = Field(default=0, description="Number of related memories")

    model_config = {"from_attributes": True}


# ─── Search schemas ───────────────────────────────────────────────────────────

class SearchFilters(BaseModel):
    """Optional filters to narrow search results."""

    tags: list[str] | None = Field(default=None, description="Filter by tags (OR logic)")
    category: str | None = Field(default=None, description="Filter by memory category")
    contributor_id: UUID | None = Field(
        default=None, description="Filter to memories this user contributed to"
    )
    date_from: datetime | None = Field(
        default=None, description="Only return memories created after this timestamp"
    )
    date_to: datetime | None = Field(
        default=None, description="Only return memories created before this timestamp"
    )
    document_id: UUID | None = Field(
        default=None, description="Filter to memories from a specific document"
    )


class SearchRequest(BaseModel):
    """
    Input for POST /v1/memories/search.

    Supports three search modes:
      hybrid (default) — Reciprocal Rank Fusion of semantic + keyword
      semantic          — pgvector cosine similarity only
      keyword           — PostgreSQL BM25 only
    """

    query: str = Field(
        min_length=1,
        max_length=1000,
        description="Natural language search query",
    )
    mode: SearchMode = Field(
        default=SearchMode.HYBRID,
        description="Search algorithm. Use 'hybrid' for best results.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return",
    )
    min_similarity: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum similarity threshold for semantic search (0.0–1.0)",
    )
    include_attribution: bool = Field(
        default=False,
        description="Include contributor attribution in results (adds latency)",
    )
    filters: SearchFilters | None = Field(default=None)


class SearchResultItem(BaseModel):
    """A single result from the search endpoint."""

    memory: MemoryResponse
    score: float = Field(description="Relevance score [0.0–1.0]. Higher = more relevant.")
    rank: int = Field(description="1-indexed result rank")
    match_type: str = Field(
        description="How this result was matched: 'semantic' | 'keyword' | 'hybrid'"
    )
    highlight: str | None = Field(
        default=None,
        description="BM25 keyword match highlight snippet (keyword/hybrid only)",
    )


class SearchResponse(BaseModel):
    """Response for POST /v1/memories/search."""

    results: list[SearchResultItem]
    total_found: int
    query: str
    mode: SearchMode
    latency_ms: float


# ─── Forward reference resolution ─────────────────────────────────────────────

class UserSummaryInline(BaseModel):
    """Minimal user info for embedding in memory version history."""

    id: UUID
    display_name: str | None
    avatar_url: str | None

    model_config = {"from_attributes": True}


MemoryVersionResponse.model_rebuild()
