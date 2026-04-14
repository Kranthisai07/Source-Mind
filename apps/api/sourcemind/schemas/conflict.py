"""Pydantic schemas for Conflict and Handoff resources."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ─── Conflict schemas ─────────────────────────────────────────────────────────

class ConflictSummary(BaseModel):
    """Minimal conflict representation for list responses."""

    id: UUID
    status: str
    conflict_type: str | None
    severity: str | None
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
    id: str
    content: str


class ConflictDetail(BaseModel):
    """Full conflict detail for GET /conflicts/:id."""

    id: str
    status: str
    conflict_type: str | None
    severity: str | None
    similarity_score: float | None
    explanation: str | None
    memory_a: MemoryRef
    memory_b: MemoryRef
    suggested_resolution: str | None
    reviewed_by: str | None
    reviewed_at: str | None
    revisit_at: str | None
    created_at: str | None


class ConflictReviewResponse(BaseModel):
    """Response for POST /conflicts/:id/review."""

    status: str
    conflict_id: str


class ConflictResolveResponse(BaseModel):
    """Response for POST /conflicts/:id/resolve."""

    status: str
    resolution_type: str


# ─── Handoff schemas ──────────────────────────────────────────────────────────

class TierOneCriticalMemory(BaseModel):
    memory_id: str
    content: str
    importance_score: float
    suggested_successor_id: str | None
    suggested_successor_name: str | None
    successor_confidence: float | None


class TierTwoMemory(BaseModel):
    memory_id: str
    content: str
    importance_score: float


class HandoffInitiateResponse(BaseModel):
    """Response for POST /workspaces/:id/handoff/initiate."""

    handoff_record_id: str
    departing_user_id: str
    departing_user_name: str | None
    total_memories: int
    tier_1_critical: list[TierOneCriticalMemory]
    tier_2_important: list[TierTwoMemory]
    tier_3_standard_count: int


class AttributionBreakdownItem(BaseModel):
    user_id: str
    name: str | None
    contribution_weight: float


class HandoffAssignResponse(BaseModel):
    """Response for POST /workspaces/:id/handoff/assign."""

    memory_id: str
    attribution: list[AttributionBreakdownItem]


class HandoffCompleteResponse(BaseModel):
    """Response for POST /workspaces/:id/handoff/complete."""

    status: str
    departing_user_id: str
    unassigned_tier_1_count: int


# ─── Version history schemas ──────────────────────────────────────────────────

class MemoryVersionEntry(BaseModel):
    """Single entry in a memory's version chain."""

    id: str
    version: int
    is_current: bool
    content: str
    created_at: datetime | None


class MemoryVersionsResponse(BaseModel):
    """Response for GET /memories/:id/versions."""

    versions: list[MemoryVersionEntry]
    total: int
