"""Pydantic v2 request/response schemas for the SourceMind API."""

from sourcemind.schemas.common import (
    APIResponse,
    ErrorDetail,
    ErrorResponse,
    PaginatedResponse,
    PaginationMeta,
    ResponseMeta,
)
from sourcemind.schemas.user import UserResponse, UserCreate
from sourcemind.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceMemberResponse,
    WorkspaceAnalyticsResponse,
)
from sourcemind.schemas.memory import (
    MemoryCreate,
    MemoryUpdate,
    MemoryResponse,
    MemoryVersionResponse,
    IngestionJobResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from sourcemind.schemas.attribution import (
    AttributionResponse,
    AttributionEditResponse,
    ContributionBreakdown,
)

__all__ = [
    "APIResponse",
    "ErrorDetail",
    "ErrorResponse",
    "PaginatedResponse",
    "PaginationMeta",
    "ResponseMeta",
    "UserResponse",
    "UserCreate",
    "WorkspaceCreate",
    "WorkspaceResponse",
    "WorkspaceMemberResponse",
    "WorkspaceAnalyticsResponse",
    "MemoryCreate",
    "MemoryUpdate",
    "MemoryResponse",
    "MemoryVersionResponse",
    "IngestionJobResponse",
    "SearchRequest",
    "SearchResponse",
    "SearchResultItem",
    "AttributionResponse",
    "AttributionEditResponse",
    "ContributionBreakdown",
]
