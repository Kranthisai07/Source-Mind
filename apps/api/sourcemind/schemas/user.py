"""Pydantic schemas for User resources."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class UserResponse(BaseModel):
    """Public user representation. Omits internal fields like clerk_id."""

    id: UUID
    email: EmailStr
    display_name: str | None
    avatar_url: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserSummary(BaseModel):
    """Minimal user representation for embedding in other responses."""

    id: UUID
    display_name: str | None
    avatar_url: str | None

    model_config = {"from_attributes": True}
