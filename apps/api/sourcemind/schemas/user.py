"""Pydantic schemas for User resources."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, HttpUrl, model_validator


class UserCreate(BaseModel):
    """
    Input schema for user provisioning (called by Clerk webhook on signup).
    Not directly exposed to end users.
    """

    clerk_id: str = Field(min_length=1, max_length=255, description="Clerk user ID")
    email: EmailStr = Field(description="Primary email address")
    display_name: str | None = Field(
        default=None, max_length=255, description="User's preferred display name"
    )
    avatar_url: str | None = Field(
        default=None, max_length=2048, description="Profile picture URL"
    )

    model_config = {"str_strip_whitespace": True}


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
