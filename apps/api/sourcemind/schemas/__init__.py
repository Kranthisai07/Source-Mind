"""Pydantic v2 request/response schemas for the SourceMind API."""

from sourcemind.schemas.attribution import (
    AttributionEditResponse,
    ContributionBreakdown,
)
from sourcemind.schemas.common import (
    APIResponse,
    ErrorDetail,
    PaginatedResponse,
    PaginationMeta,
    ResponseMeta,
)
from sourcemind.schemas.conflict import (
    ConflictDetail,
    ConflictListResponse,
    ConflictResolveResponse,
    ConflictReviewResponse,
    ConflictSummary,
    HandoffAssignResponse,
    HandoffCompleteResponse,
    HandoffInitiateResponse,
    MemoryVersionEntry,
    MemoryVersionsResponse,
)
from sourcemind.schemas.connector import (
    ConnectorCreateRequest,
    ConnectorListResponse,
    ConnectorResponse,
    ConnectorUpdateRequest,
    SyncLogResponse,
    SyncLogsResponse,
    SyncTriggerRequest,
    SyncTriggerResponse,
)
from sourcemind.schemas.memory import (
    IngestionJobResponse,
    MemoryCreate,
    MemoryResponse,
    MemoryUpdate,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from sourcemind.schemas.user import UserResponse, UserSummary
from sourcemind.schemas.workspace import (
    WorkspaceAnalyticsResponse,
    WorkspaceCreate,
    WorkspaceMemberResponse,
    WorkspaceResponse,
)

__all__ = [
    "APIResponse",
    "AttributionEditResponse",
    "ConflictDetail",
    "ConflictListResponse",
    "ConflictResolveResponse",
    "ConflictReviewResponse",
    "ConflictSummary",
    "ConnectorCreateRequest",
    "ConnectorListResponse",
    "ConnectorResponse",
    "ConnectorUpdateRequest",
    "ContributionBreakdown",
    "ErrorDetail",
    "HandoffAssignResponse",
    "HandoffCompleteResponse",
    "HandoffInitiateResponse",
    "IngestionJobResponse",
    "MemoryCreate",
    "MemoryResponse",
    "MemoryUpdate",
    "MemoryVersionEntry",
    "MemoryVersionsResponse",
    "PaginatedResponse",
    "PaginationMeta",
    "ResponseMeta",
    "SearchRequest",
    "SearchResponse",
    "SearchResultItem",
    "SyncLogResponse",
    "SyncLogsResponse",
    "SyncTriggerRequest",
    "SyncTriggerResponse",
    "UserResponse",
    "UserSummary",
    "WorkspaceAnalyticsResponse",
    "WorkspaceCreate",
    "WorkspaceMemberResponse",
    "WorkspaceResponse",
]
