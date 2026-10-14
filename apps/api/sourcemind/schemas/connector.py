"""Pydantic schemas for Connector resources."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic import (
    ValidationError as PydanticValidationError,
)

from sourcemind.core.config import get_settings
from sourcemind.schemas.common import ItemList

ConnectorTypeLiteral = Literal["github", "discord", "slack", "notion"]
ConnectorStatusLiteral = Literal["active", "paused", "error", "disconnected", "paused_budget"]
# States a caller may request through PATCH; the others are server-owned.
ConnectorRequestedStatusLiteral = Literal["active", "paused"]
SyncTypeLiteral = Literal["full", "incremental"]
SyncStatusLiteral = Literal[
    "running", "completed", "failed", "continuing", "interrupted", "aborted"
]
SyncTriggerLiteral = Literal["manual", "scheduled", "recovery"]
ScopeStatusLiteral = Literal[
    "ok", "access_lost", "authorization_unverified", "reauthorization_required", "error"
]
ArtifactScopeLiteral = Literal[
    "commits",
    "pull_requests",
    "issues",
    "issue_comments",
    "review_comments",
    "review_reconcile",
]
ConnectorStateLiteral = Literal[
    "idle",
    "syncing",
    "needs_attention",
    "paused",
    "paused_budget",
    "error",
    "disconnected",
]

_SENSITIVE_CONFIG_PARTS = (
    "token",
    "secret",
    "password",
    "private_key",
    "api_key",
    "credential",
    "webhook",
)

_REPO_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/(?!\.{1,2}$)[A-Za-z0-9._-]{1,100}$"
)


def _redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(part in str(key).lower() for part in _SENSITIVE_CONFIG_PARTS)
                else _redact_config(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


# ─── Strict per-connector-type config ─────────────────────────────────────────

class GitHubConnectorConfig(BaseModel):
    """The only settings a GitHub connector accepts.

    ``extra="forbid"`` is the control that keeps secrets out of ``config``: any key that
    is not listed here (``token``, ``installation_id``, ``authorized_github_user_id`` ...)
    is a validation error. The installation binding is a server-set column.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    repos: list[str] = Field(min_length=1, description="'owner/name' repositories to import")
    artifact_types: list[ArtifactScopeLiteral] | None = Field(
        default=None, description="Subset of the supported scopes; omit for all"
    )
    consent_workspace_readable: Literal[True] = Field(
        description="Admin acknowledges every workspace member can read imported content"
    )

    @field_validator("repos")
    @classmethod
    def _validate_repos(cls, value: list[str]) -> list[str]:
        limit = get_settings().connector_max_repos
        if len(value) > limit:
            raise ValueError(f"at most {limit} repositories per connector")
        seen: set[str] = set()
        unique: list[str] = []
        for repo in value:
            if not _REPO_PATTERN.fullmatch(repo):
                raise ValueError("repositories must be written as 'owner/name'")
            key = repo.casefold()
            if key in seen:
                raise ValueError("duplicate repository")
            seen.add(key)
            unique.append(repo)
        return unique

    @field_validator("artifact_types")
    @classmethod
    def _validate_artifact_types(
        cls, value: list[str] | None
    ) -> list[str] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("artifact_types must not be empty; omit it for all types")
        return list(dict.fromkeys(value))


def parse_github_config(raw: dict[str, Any]) -> GitHubConnectorConfig:
    """Validate ``raw``; raise ``ValueError`` listing field paths and codes only.

    The pydantic message is deliberately not forwarded: it can echo the submitted value,
    and a rejected key may be a secret.
    """
    try:
        return GitHubConnectorConfig.model_validate(raw)
    except PydanticValidationError as exc:
        problems = sorted(
            {
                f"{'.'.join(str(part) for part in error['loc']) or 'config'}: {error['type']}"
                for error in exc.errors()
            }
        )
        raise ValueError("invalid github config (" + "; ".join(problems) + ")") from None


# ─── Request schemas ──────────────────────────────────────────────────────────

class ConnectorCreateRequest(BaseModel):
    """Payload for creating a new connector configuration."""

    connector_type: ConnectorTypeLiteral = Field(description="Only 'github' is supported")
    display_name: str = Field(min_length=1, max_length=255)
    config: dict[str, Any] = Field(
        default_factory=dict, description="Connector-specific settings (strictly validated)"
    )
    ticket_id: str | None = Field(
        default=None,
        min_length=16,
        max_length=256,
        description="Authorization ticket returned by POST /connectors/github/callback",
    )

    @model_validator(mode="after")
    def _validate_config_for_type(self) -> ConnectorCreateRequest:
        if self.connector_type != "github":
            raise ValueError(f"connector type '{self.connector_type}' is not supported yet")
        if not self.ticket_id:
            raise ValueError("ticket_id is required to connect GitHub")
        self.config = parse_github_config(self.config).model_dump(exclude_none=True)
        return self


class ConnectorUpdateRequest(BaseModel):
    """Payload for updating a connector configuration."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = Field(
        default=None, description="May only narrow 'repos'; adding a repository needs a new connect"
    )
    status: ConnectorRequestedStatusLiteral | None = Field(default=None)


class SyncTriggerRequest(BaseModel):
    """Payload for manually triggering a connector sync."""

    sync_type: SyncTypeLiteral = Field(
        default="incremental", description="'full' or 'incremental'"
    )


class GitHubCallbackRequest(BaseModel):
    """Body of POST /connectors/github/callback."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=512, repr=False)
    state: str = Field(min_length=1, max_length=300)
    installation_id: int | None = Field(
        default=None,
        gt=0,
        description="Installation chosen by the user; required when several are reachable",
    )


# ─── Response schemas ─────────────────────────────────────────────────────────

class ConnectorResponse(BaseModel):
    """Single connector configuration response."""

    id: UUID
    workspace_id: UUID
    connector_type: ConnectorTypeLiteral
    display_name: str
    config: dict[str, Any]
    status: ConnectorStatusLiteral
    last_sync_at: datetime | None
    next_sync_at: datetime | None
    created_at: datetime
    installation_id: int | None = None
    authorized_github_user_id: int | None = None
    created_by: UUID | None = None
    generation: int = 0
    last_success_at: datetime | None = None
    last_error_code: str | None = None
    sync_interval_minutes: int | None = None

    model_config = {"from_attributes": True}

    @field_serializer("config")
    def serialize_config(self, value: dict[str, Any]) -> dict[str, Any]:
        """Never return stored connector credentials from API responses."""
        return _redact_config(value)


class ConnectorListResponse(ItemList[ConnectorResponse]):
    """List of connectors for a workspace."""


class SyncLogResponse(BaseModel):
    """Single sync log entry."""

    id: UUID
    connector_id: UUID
    sync_type: SyncTypeLiteral
    status: SyncStatusLiteral
    artifacts_found: int
    artifacts_new: int
    artifacts_skipped: int
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
    trigger: SyncTriggerLiteral = "manual"
    artifacts_updated: int = 0
    artifacts_failed: int = 0
    artifacts_filtered: dict[str, int] = Field(default_factory=dict)
    error_code: str | None = None

    model_config = {"from_attributes": True}

    @field_serializer("error_message")
    def serialize_error_message(self, value: str | None) -> str | None:
        """Keep provider errors in server logs, not tenant-facing responses."""
        return "Connector sync failed." if value else None


class SyncTriggerResponse(BaseModel):
    """Response after triggering a connector sync."""

    task_id: str
    connector_id: UUID
    sync_type: SyncTypeLiteral
    message: str


class SyncLogsResponse(ItemList[SyncLogResponse]):
    """Paginated sync logs for a connector."""


# ── Connect flow ──────────────────────────────────────────────────────────────

class GitHubAuthorizeResponse(BaseModel):
    """GET /workspaces/{id}/connectors/github/authorize."""

    authorize_url: str
    state: str
    expires_in_seconds: int


class GitHubRepoOption(BaseModel):
    """One repository the connecting user can reach through the installation."""

    full_name: str
    private: bool
    default_branch: str | None = None


class GitHubCallbackResponse(BaseModel):
    """POST /connectors/github/callback: the repository picker and the create ticket."""

    ticket_id: str
    expires_in_seconds: int
    workspace_id: UUID
    installation_id: int
    github_user_id: int
    github_login: str | None = None
    repositories: list[GitHubRepoOption]
    truncated: bool = False


# ── Status ────────────────────────────────────────────────────────────────────

class ScopeStatusItem(BaseModel):
    """One (repo, artifact_type) scope of a connector."""

    repo: str
    artifact_type: str
    status: ScopeStatusLiteral
    phase: str
    listing_complete: bool
    last_error_code: str | None = None
    last_progress_at: datetime | None = None
    authorization_verified_at: datetime | None = None
    window_low: datetime | None = None
    window_high: datetime | None = None


class DocumentCounters(BaseModel):
    """Imported documents of this connector by pipeline state."""

    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    quarantined: int = 0
    zero_facts: int = 0


class CurrentRunProgress(BaseModel):
    """Progress of the sync log that is still running."""

    sync_log_id: UUID
    trigger: SyncTriggerLiteral
    sync_type: SyncTypeLiteral
    started_at: datetime
    artifacts_found: int
    artifacts_new: int
    artifacts_updated: int
    artifacts_skipped: int
    artifacts_failed: int
    artifacts_filtered: dict[str, int] = Field(default_factory=dict)


class ConnectorStatusResponse(BaseModel):
    """GET /connectors/{id}/status."""

    connector_id: UUID
    workspace_id: UUID
    connector_type: ConnectorTypeLiteral
    display_name: str
    status: ConnectorStatusLiteral
    state: ConnectorStateLiteral
    generation: int
    last_sync_at: datetime | None
    next_sync_at: datetime | None
    last_success_at: datetime | None
    last_error_code: str | None
    current_run: CurrentRunProgress | None
    scopes: list[ScopeStatusItem]
    documents: DocumentCounters
    filtered: dict[str, int] = Field(
        default_factory=dict, description="Skip reasons summed over all sync logs"
    )
    recent_logs: list[SyncLogResponse]


# ── Lifecycle ─────────────────────────────────────────────────────────────────

class DisconnectResponse(BaseModel):
    """DELETE /connectors/{id}: a soft disconnect."""

    connector_id: UUID
    status: Literal["disconnected"]
    generation: int
    remove_memories: bool
    documents_considered: int = 0
    memories_retired: int = 0
    memories_flagged: int = 0


class RetryFailedResponse(BaseModel):
    """POST /connectors/{id}/retry-failed."""

    connector_id: UUID
    eligible: int
    requeued: int
    refused: int
    remaining: int = Field(description="Eligible documents beyond this call's batch limit")


class RescanResponse(BaseModel):
    """POST /connectors/{id}/rescan."""

    connector_id: UUID
    scopes_marked: int
    message: str
