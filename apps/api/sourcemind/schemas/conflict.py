"""Pydantic schemas for Conflict and Handoff resources."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

# Status / type literals — kept in sync with model enums in
# sourcemind.models.memory_conflict (ConflictStatus, ConflictType, ConflictSeverity).
ConflictStatusLiteral = Literal["open", "under_review", "resolved", "deferred", "dismissed"]
ConflictTypeLiteral = Literal["contradiction", "duplication", "ambiguity"]
ConflictSeverityLiteral = Literal["low", "medium", "critical"]
ResolutionTypeLiteral = Literal["kept_a", "kept_b", "merged", "split", "deferred"]


# ─── Conflict schemas ─────────────────────────────────────────────────────────

class ConflictSummary(BaseModel):
    """Minimal conflict representation for list responses."""

    id: UUID
    status: ConflictStatusLiteral
    conflict_type: ConflictTypeLiteral | None
    severity: ConflictSeverityLiteral | None
    similarity_score: float | None
    explanation: str | None
    memory_a_id: UUID
    memory_a_content: str
    memory_b_id: UUID
    memory_b_content: str
    created_at: datetime | None

    model_config = {"from_attributes": True}


class ConflictListResponse(BaseModel):
    """Response for GET /workspaces/:id/conflicts."""

    conflicts: list[ConflictSummary]
    total: int
    next_cursor: str | None = None


class MemoryRef(BaseModel):
    id: UUID
    content: str


class ConflictDetail(BaseModel):
    """Full conflict detail for GET /conflicts/:id."""

    id: UUID
    status: ConflictStatusLiteral
    conflict_type: ConflictTypeLiteral | None
    severity: ConflictSeverityLiteral | None
    similarity_score: float | None
    explanation: str | None
    memory_a: MemoryRef
    memory_b: MemoryRef
    suggested_resolution: dict[str, Any] | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    revisit_at: datetime | None
    created_at: datetime | None


class ConflictReviewResponse(BaseModel):
    """Response for POST /conflicts/:id/review."""

    status: Literal["under_review", "no_change"]
    conflict_id: UUID


class ConflictResolveResponse(BaseModel):
    """Response for POST /conflicts/:id/resolve."""

    status: Literal["ok"]
    resolution_type: ResolutionTypeLiteral


# ─── Handoff schemas ──────────────────────────────────────────────────────────

class TierOneCriticalMemory(BaseModel):
    memory_id: UUID
    content: str
    importance_score: float
    suggested_successor_id: UUID | None
    suggested_successor_name: str | None
    successor_confidence: float | None


class TierTwoMemory(BaseModel):
    memory_id: UUID
    content: str
    importance_score: float


class HandoffInitiateResponse(BaseModel):
    """Response for POST /workspaces/:id/handoff/initiate."""

    handoff_record_id: UUID
    departing_user_id: UUID
    departing_user_name: str | None
    total_memories: int
    tier_1_critical: list[TierOneCriticalMemory]
    tier_2_important: list[TierTwoMemory]
    tier_3_standard_count: int


class AttributionBreakdownItem(BaseModel):
    user_id: UUID
    name: str | None
    contribution_weight: float


class HandoffAssignResponse(BaseModel):
    """Response for POST /workspaces/:id/handoff/assign."""

    memory_id: UUID
    attribution: list[AttributionBreakdownItem]


class HandoffCompleteResponse(BaseModel):
    """Response for POST /workspaces/:id/handoff/complete."""

    status: Literal["completed", "no_change"]
    departing_user_id: UUID
    unassigned_tier_1_count: int


# ─── Version history schemas ──────────────────────────────────────────────────

class MemoryVersionEntry(BaseModel):
    """Single entry in a memory's version chain."""

    id: UUID
    version: int
    is_current: bool
    content: str
    created_at: datetime | None


class MemoryVersionsResponse(BaseModel):
    """Response for GET /memories/:id/versions."""

    versions: list[MemoryVersionEntry]
    total: int
