"""Pydantic schemas for Workspace resources."""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from sourcemind.models.workspace import WorkspaceRole
from sourcemind.schemas.user import UserSummary

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class WorkspaceCreate(BaseModel):
    """Input for creating a new workspace."""

    name: str = Field(min_length=2, max_length=255, description="Human-readable workspace name")
    slug: str = Field(
        min_length=2,
        max_length=100,
        description="URL-safe identifier. Must be lowercase alphanumeric with hyphens.",
    )
    description: str | None = Field(
        default=None, max_length=1000, description="Optional workspace description"
    )

    model_config = {"str_strip_whitespace": True}

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        if not _SLUG_PATTERN.match(v):
            raise ValueError(
                "Slug must be lowercase alphanumeric with hyphens only, "
                "and cannot start or end with a hyphen. Example: 'engineering-team'"
            )
        return v


class WorkspaceUpdate(BaseModel):
    """Partial update schema for workspace metadata."""

    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=1000)

    model_config = {"str_strip_whitespace": True}


class WorkspaceResponse(BaseModel):
    """Full workspace representation returned to API consumers."""

    id: UUID
    organization_id: UUID
    name: str
    slug: str
    description: str | None
    created_at: datetime
    updated_at: datetime | None
    member_count: int | None = None  # Populated by service layer

    model_config = {"from_attributes": True}


class WorkspaceMemberResponse(BaseModel):
    """Workspace member with role information."""

    user: UserSummary
    role: WorkspaceRole
    joined_at: datetime

    model_config = {"from_attributes": True}


class ContributorStats(BaseModel):
    """Contribution statistics for a single workspace member."""

    user: UserSummary
    memory_count: int = Field(description="Number of memories this user has contributed to")
    total_weight: float = Field(description="Sum of contribution weights across all memories")
    avg_weight: float = Field(description="Average contribution weight per memory")
    primary_contributor_count: int = Field(
        description="Number of memories where this user is the primary contributor (weight > 0.5)"
    )


class KnowledgeGap(BaseModel):
    """A detected knowledge gap — topic with low or no documented coverage."""

    topic: str
    gap_score: float = Field(description="0.0 = well documented, 1.0 = complete gap")
    suggested_owners: list[UserSummary] = Field(
        description="Users most likely to fill this gap based on past contributions"
    )


class WorkspaceAnalyticsResponse(BaseModel):
    """
    Workspace knowledge analytics.

    Returned by GET /v1/workspaces/:id/analytics
    Used for the contribution map and knowledge gap visualizations.
    """

    workspace_id: UUID
    total_memories: int
    total_documents: int
    active_contributors: int
    contributor_stats: list[ContributorStats]
    knowledge_gaps: list[KnowledgeGap]
    memories_last_30_days: int
    open_conflicts: int
    computed_at: datetime
