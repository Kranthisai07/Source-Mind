"""Pydantic schemas for Attribution resources."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from sourcemind.schemas.user import UserSummary


class SignalBreakdown(BaseModel):
    """
    Per-signal attribution scores for a single contributor.

    Provided for transparency and debugging. Weights:
      char_diff:   35%
      semantic:    30%
      temporal:    15%
      structural:  10%
      approval:    10%
    """

    char_diff_score: float | None = Field(
        default=None, description="Character diff signal [0.0–1.0], weight=35%"
    )
    semantic_score: float | None = Field(
        default=None, description="SBERT semantic similarity [0.0–1.0], weight=30%"
    )
    temporal_score: float | None = Field(
        default=None, description="Temporal primacy [0.0–1.0], weight=15%"
    )
    structural_score: float | None = Field(
        default=None, description="New named entities [0.0–1.0], weight=10%"
    )
    approval_score: float | None = Field(
        default=None, description="Explicit approval [0.0 or 0.1], weight=10%"
    )


class ContributionBreakdown(BaseModel):
    """
    Attribution breakdown for a single contributor to a memory.

    This is the primary unit surfaced in the UI — a contributor's share
    of a memory expressed as a normalized weight.
    """

    user: UserSummary
    contribution_weight: float = Field(
        description="Normalized contribution share [0.0–1.0]. All contributors sum to 1.0.",
        ge=0.0,
        le=1.0,
    )
    percentage: float = Field(
        description="Contribution as a percentage (0–100)",
        ge=0.0,
        le=100.0,
    )
    signals: SignalBreakdown | None = Field(
        default=None,
        description="Per-signal breakdown. Only included for owner/admin roles.",
    )
    is_primary: bool = Field(
        description="True if this user contributed more than 50% (primary author)"
    )

    model_config = {"from_attributes": True}


class AttributionEditResponse(BaseModel):
    """A single edit record from a memory's edit history."""

    id: UUID
    memory_id: UUID
    editor: UserSummary
    content_before: str | None
    content_after: str
    edit_position: int
    action_type: str
    created_at: datetime

    model_config = {"from_attributes": True}
