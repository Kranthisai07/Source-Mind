"""
SourceMind custom exception hierarchy with structured error codes.

Error code ranges:
  SM001–SM009: Authentication / Authorization
  SM010–SM019: Validation / Input errors
  SM020–SM029: Resource not found
  SM030–SM039: Conflict / Duplicate errors
  SM040–SM049: Processing / Pipeline errors
  SM050–SM059: External service errors
  SM090–SM099: Internal / Unexpected errors

All exceptions map to HTTP status codes via the global exception handler
registered in main.py. Never raise HTTPException directly in service code —
raise domain exceptions and let the handler translate them.
"""

from enum import StrEnum
from http import HTTPStatus
from typing import Any


class ErrorCode(StrEnum):
    """
    Canonical SourceMind error codes.
    Format: SM + zero-padded 3-digit number.
    """

    # Authentication / Authorization (001–009)
    UNAUTHORIZED = "SM001"
    FORBIDDEN = "SM002"
    TOKEN_EXPIRED = "SM003"
    TOKEN_INVALID = "SM004"
    WORKSPACE_ACCESS_DENIED = "SM005"

    # Validation / Input (010–019)
    VALIDATION_ERROR = "SM010"
    INVALID_CONTENT_TYPE = "SM011"
    CONTENT_TOO_LARGE = "SM012"
    INVALID_IDEMPOTENCY_KEY = "SM013"
    INVALID_CURSOR = "SM014"

    # Resource not found (020–029)
    MEMORY_NOT_FOUND = "SM020"
    DOCUMENT_NOT_FOUND = "SM021"
    WORKSPACE_NOT_FOUND = "SM022"
    USER_NOT_FOUND = "SM023"
    JOB_NOT_FOUND = "SM024"
    ORGANIZATION_NOT_FOUND = "SM025"

    # Conflict / Duplicate (030–039)
    MEMORY_DUPLICATE = "SM030"
    WORKSPACE_SLUG_TAKEN = "SM031"
    ORGANIZATION_SLUG_TAKEN = "SM032"
    IDEMPOTENCY_CONFLICT = "SM033"

    # Processing / Pipeline (040–049)
    INGESTION_FAILED = "SM040"
    EXTRACTION_FAILED = "SM041"
    EMBEDDING_FAILED = "SM042"
    CHUNKING_FAILED = "SM043"

    # External service (050–059)
    OPENAI_ERROR = "SM050"
    ANTHROPIC_ERROR = "SM051"
    NEO4J_ERROR = "SM052"
    S3_ERROR = "SM053"
    KAFKA_ERROR = "SM054"

    # Internal (090–099)
    INTERNAL_ERROR = "SM090"
    DATABASE_ERROR = "SM091"
    CONFIGURATION_ERROR = "SM092"
    NOT_IMPLEMENTED = "SM099"


class SourceMindError(Exception):
    """
    Base class for all SourceMind domain exceptions.

    Attributes:
        code: Machine-readable error code (e.g. "SM020")
        message: Human-readable description
        details: Optional structured details (request IDs, field names, etc.)
        http_status: HTTP status code for the error response
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    http_status: int = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str,
        details: Any = None,
        *,
        code: ErrorCode | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


# ─── Auth exceptions ──────────────────────────────────────────────────────────

class UnauthorizedError(SourceMindError):
    """No valid authentication credentials provided."""
    code = ErrorCode.UNAUTHORIZED
    http_status = HTTPStatus.UNAUTHORIZED


class ForbiddenError(SourceMindError):
    """Authenticated but not permitted to perform this action."""
    code = ErrorCode.FORBIDDEN
    http_status = HTTPStatus.FORBIDDEN


class TokenExpiredError(SourceMindError):
    """JWT token has expired."""
    code = ErrorCode.TOKEN_EXPIRED
    http_status = HTTPStatus.UNAUTHORIZED


class TokenInvalidError(SourceMindError):
    """JWT token is malformed or has an invalid signature."""
    code = ErrorCode.TOKEN_INVALID
    http_status = HTTPStatus.UNAUTHORIZED


class WorkspaceAccessDeniedError(SourceMindError):
    """User does not belong to the requested workspace."""
    code = ErrorCode.WORKSPACE_ACCESS_DENIED
    http_status = HTTPStatus.FORBIDDEN


# ─── Validation exceptions ────────────────────────────────────────────────────

class ValidationError(SourceMindError):
    """Input data failed validation."""
    code = ErrorCode.VALIDATION_ERROR
    http_status = HTTPStatus.UNPROCESSABLE_ENTITY


class ContentTooLargeError(SourceMindError):
    """Submitted content exceeds the allowed size limit."""
    code = ErrorCode.CONTENT_TOO_LARGE
    http_status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE


class InvalidIdempotencyKeyError(SourceMindError):
    """Idempotency-Key header is missing or malformed."""
    code = ErrorCode.INVALID_IDEMPOTENCY_KEY
    http_status = HTTPStatus.BAD_REQUEST


# ─── Not found exceptions ─────────────────────────────────────────────────────

class MemoryNotFoundError(SourceMindError):
    """Memory with the given ID does not exist or was soft-deleted."""
    code = ErrorCode.MEMORY_NOT_FOUND
    http_status = HTTPStatus.NOT_FOUND


class DocumentNotFoundError(SourceMindError):
    """Document with the given ID does not exist."""
    code = ErrorCode.DOCUMENT_NOT_FOUND
    http_status = HTTPStatus.NOT_FOUND


class WorkspaceNotFoundError(SourceMindError):
    """Workspace with the given ID or slug does not exist."""
    code = ErrorCode.WORKSPACE_NOT_FOUND
    http_status = HTTPStatus.NOT_FOUND


class UserNotFoundError(SourceMindError):
    """User with the given ID or Clerk ID does not exist."""
    code = ErrorCode.USER_NOT_FOUND
    http_status = HTTPStatus.NOT_FOUND


class JobNotFoundError(SourceMindError):
    """Ingestion job with the given ID does not exist."""
    code = ErrorCode.JOB_NOT_FOUND
    http_status = HTTPStatus.NOT_FOUND


# ─── Conflict exceptions ──────────────────────────────────────────────────────

class DuplicateMemoryError(SourceMindError):
    """Content has already been ingested (SHA-256 match)."""
    code = ErrorCode.MEMORY_DUPLICATE
    http_status = HTTPStatus.CONFLICT


class WorkspaceSlugTakenError(SourceMindError):
    """Workspace slug is already in use within this organization."""
    code = ErrorCode.WORKSPACE_SLUG_TAKEN
    http_status = HTTPStatus.CONFLICT


class IdempotencyConflictError(SourceMindError):
    """Same Idempotency-Key was used with different request body."""
    code = ErrorCode.IDEMPOTENCY_CONFLICT
    http_status = HTTPStatus.CONFLICT


# ─── Pipeline exceptions ──────────────────────────────────────────────────────

class IngestionError(SourceMindError):
    """Document ingestion pipeline failed."""
    code = ErrorCode.INGESTION_FAILED
    http_status = HTTPStatus.INTERNAL_SERVER_ERROR


class EmbeddingError(SourceMindError):
    """Embedding generation failed."""
    code = ErrorCode.EMBEDDING_FAILED
    http_status = HTTPStatus.INTERNAL_SERVER_ERROR


# ─── External service exceptions ──────────────────────────────────────────────

class OpenAIError(SourceMindError):
    """OpenAI API call failed."""
    code = ErrorCode.OPENAI_ERROR
    http_status = HTTPStatus.BAD_GATEWAY


class AnthropicError(SourceMindError):
    """Anthropic API call failed."""
    code = ErrorCode.ANTHROPIC_ERROR
    http_status = HTTPStatus.BAD_GATEWAY


# ─── Internal exceptions ──────────────────────────────────────────────────────

class InternalError(SourceMindError):
    """Unexpected internal server error."""
    code = ErrorCode.INTERNAL_ERROR
    http_status = HTTPStatus.INTERNAL_SERVER_ERROR


class DatabaseError(SourceMindError):
    """Database operation failed."""
    code = ErrorCode.DATABASE_ERROR
    http_status = HTTPStatus.INTERNAL_SERVER_ERROR


class NotImplementedFeatureError(SourceMindError):
    """Feature is not yet implemented."""
    code = ErrorCode.NOT_IMPLEMENTED
    http_status = HTTPStatus.NOT_IMPLEMENTED
