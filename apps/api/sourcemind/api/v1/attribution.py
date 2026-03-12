"""
Attribution endpoints — read-only access to contribution data.

GET /v1/memories/:id/attribution        → current attribution breakdown
GET /v1/memories/:id/attribution/history → attribution timeline
GET /v1/memories/:id/edits              → edit history with contributor info
"""

from uuid import UUID

import structlog
from fastapi import APIRouter

from sourcemind.core.dependencies import CurrentUser, DBSession, PaginationDep, RequestID
from sourcemind.core.exceptions import NotImplementedFeatureError
from sourcemind.schemas.attribution import AttributionEditResponse, ContributionBreakdown
from sourcemind.schemas.common import APIResponse, PaginatedResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/memories", tags=["attribution"])


@router.get(
    "/{memory_id}/attribution",
    response_model=APIResponse[list[ContributionBreakdown]],
    summary="Get current attribution breakdown",
    description=(
        "Returns the normalized contribution weights for all contributors to this memory. "
        "All weights sum to 1.0. Minimum 2% floor for any contributor with ≥1 substantive edit."
    ),
)
async def get_memory_attribution(
    memory_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
) -> APIResponse[list[ContributionBreakdown]]:
    """Return the current attribution breakdown for a memory."""
    raise NotImplementedFeatureError(
        "Attribution retrieval will be implemented in Phase 2."
    )


@router.get(
    "/{memory_id}/edits",
    response_model=PaginatedResponse[AttributionEditResponse],
    summary="Get edit history",
    description="Returns the full edit history for a memory, used to audit the attribution chain.",
)
async def get_memory_edits(
    memory_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    request_id: RequestID,
    pagination: PaginationDep,
) -> PaginatedResponse[AttributionEditResponse]:
    """Return the edit history (attribution_edits) for a memory."""
    raise NotImplementedFeatureError(
        "Edit history will be implemented in Phase 2."
    )
