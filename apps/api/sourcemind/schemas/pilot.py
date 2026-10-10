"""Schemas for operator-managed pilot evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class RatingSource(StrEnum):
    INDEPENDENT = "independent"
    SELF = "self"
    EXTERNAL = "external"


class SearchRatingCreate(BaseModel):
    search_event_id: UUID
    memory_id: UUID
    rater_user_id: UUID | None = None
    rater_pseudonym: str | None = Field(default=None, min_length=1, max_length=200)
    rating_source: RatingSource
    allocation: float | None = Field(default=None, ge=0.0, le=100.0)
    exclusion_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_rating_contract(self) -> SearchRatingCreate:
        has_allocation = self.allocation is not None
        has_exclusion = self.exclusion_reason is not None
        if has_allocation == has_exclusion:
            raise ValueError("Provide exactly one of allocation or exclusion_reason.")

        if self.rating_source == RatingSource.EXTERNAL:
            if self.rater_user_id is not None or self.rater_pseudonym is None:
                raise ValueError(
                    "External ratings require a pseudonym and cannot name a workspace user."
                )
        else:
            if self.rater_user_id is None:
                raise ValueError("Self and independent ratings require rater_user_id.")
            if self.rater_pseudonym is not None:
                raise ValueError(
                    "Self and independent ratings use rater_user_id, not a pseudonym."
                )
        return self


class SearchRatingResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    search_event_id: UUID
    memory_id: UUID
    result_rank: int
    rater_user_id: UUID | None
    rater_pseudonym: str | None
    rating_source: RatingSource
    allocation: float | None
    exclusion_reason: str | None
    idempotency_key: UUID
    recorded_by_user_id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}
