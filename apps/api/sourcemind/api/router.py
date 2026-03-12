"""
Central API router — mounts all v1 route groups.

All versioned routes live under /v1/. The health check lives at /health
(no version prefix — consumed by infrastructure, not clients).
"""

from fastapi import APIRouter

from sourcemind.api.v1 import (
    analytics,
    attribution,
    conflicts,
    health,
    memories,
    search,
    team,
    workspaces,
)

api_router = APIRouter()

# Health check — no version prefix, consumed by infra
api_router.include_router(health.router)

# v1 API routes
_v1_prefix = "/v1"

api_router.include_router(memories.router, prefix=_v1_prefix)
api_router.include_router(search.router, prefix=_v1_prefix)
api_router.include_router(workspaces.router, prefix=_v1_prefix)
api_router.include_router(attribution.router, prefix=_v1_prefix)
api_router.include_router(team.router, prefix=_v1_prefix)
api_router.include_router(team.handoff_router, prefix=_v1_prefix)
api_router.include_router(conflicts.router, prefix=_v1_prefix)
api_router.include_router(analytics.router, prefix=_v1_prefix)
