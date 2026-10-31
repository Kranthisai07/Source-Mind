"""Request and response shapes for workspace invitations (D-065)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# smi_ + token_urlsafe(32), which is exactly 43 URL-safe characters.
TOKEN_PATTERN = r"^smi_[A-Za-z0-9_-]{43}$"  # noqa: S105  (a format, not a secret)


class InvitationCreate(BaseModel):
    """Invite an email address. Only founders may invite as admin or founder."""

    email: EmailStr
    role: Literal["owner", "admin", "member", "viewer"] = "member"

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("email")
    @classmethod
    def _ascii_lowercase(cls, value: str) -> str:
        # Same reasoning as WorkspaceMemberAdd: lower() and casefold() disagree on non-ASCII, so
        # an address could compare equal in one place and not another. Refuse rather than guess.
        if not value.isascii():
            raise ValueError("Only ASCII email addresses can be invited.")
        return value.lower()


class InvitationResponse(BaseModel):
    """A pending invitation. Never carries the token."""

    id: uuid.UUID
    email: str
    role: Literal["owner", "admin", "member", "viewer"]
    created_at: datetime
    expires_at: datetime
    invited_by: uuid.UUID

    model_config = ConfigDict(from_attributes=True)


class InvitationCreated(InvitationResponse):
    """The one response that carries the token. It is shown once and cannot be read again."""

    token: str
    accept_path: str = Field(description="Path on the web app that accepts this invitation.")


class InvitationAccept(BaseModel):
    token: str = Field(pattern=TOKEN_PATTERN)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InvitationAccepted(BaseModel):
    workspace_id: uuid.UUID
    role: Literal["owner", "admin", "member", "viewer"]
