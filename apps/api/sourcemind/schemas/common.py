"""
Common response envelope schemas.

All API responses follow a consistent envelope structure:
  Success:  { data: T, meta: ResponseMeta }
  Paginated: { data: list[T], meta: PaginationMeta }
  Error:    { error: ErrorDetail }

This never changes without a version bump. The meta block provides
request tracing, timing, and pagination cursors.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ResponseMeta(BaseModel):
    """Metadata included in every successful response."""

    request_id: str = Field(description="Correlation ID for distributed tracing")
    timestamp: datetime = Field(description="Server-side response timestamp (UTC)")
    latency_ms: float = Field(description="Total server processing time in milliseconds")

    model_config = {"frozen": True}


class PaginationMeta(ResponseMeta):
    """
    Extended metadata for list endpoints with keyset pagination.

    next_cursor is opaque — clients must not parse or construct it.
    Pass it verbatim as the `cursor` query parameter for the next page.
    """

    total_count: int | None = Field(
        default=None,
        description="Total matching records (expensive — only included when explicitly requested)",
    )
    returned_count: int = Field(description="Number of items in this response")
    has_more: bool = Field(description="True if there are more pages available")
    next_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page. NULL when has_more=False.",
    )
    limit: int = Field(description="Page size used for this request")


class APIResponse[T](BaseModel):
    """
    Standard success response envelope.

    Usage in route handlers:
        return APIResponse(data=result, meta=ResponseMeta(...))
    """

    data: T
    meta: ResponseMeta


class PaginatedResponse[T](BaseModel):
    """Standard paginated list response envelope (data + cursor metadata)."""

    data: list[T]
    meta: PaginationMeta


class ItemList[T](BaseModel):
    """
    Lightweight `{items, total}` envelope used by simple list endpoints
    that do not need cursor-based pagination metadata.

    Prefer PaginatedResponse[T] for endpoints that support keyset pagination.
    """

    items: list[T]
    total: int


class ErrorDetail(BaseModel):
    """
    Structured error information.

    code:    Machine-readable SM-prefixed error code
    message: Human-readable description safe to display to users
    details: Optional structured context (field names, values, etc.)
             Null in production for 5xx errors to avoid leaking internals.
    """

    code: str = Field(description="SM-prefixed error code, e.g. 'SM020'")
    message: str = Field(description="Human-readable error description")
    details: dict[str, Any] | None = Field(default=None)
    request_id: str = Field(description="Correlation ID for support lookup")

