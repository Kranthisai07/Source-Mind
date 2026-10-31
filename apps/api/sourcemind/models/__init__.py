"""
SQLAlchemy ORM models for SourceMind.

Every model module must be imported here so that Base.metadata sees all of
them during Alembic autogenerate. It is the IMPORT that matters, not its
position: relationships are declared as string forward references and are
resolved by the mapper registry at configure_mappers() time, so this block is
safe to keep alphabetically sorted (and ruff's isort rule will sort it).
"""

from sourcemind.models.attribution import (
    Attribution,
    AttributionActionType,
    AttributionEdit,
)
from sourcemind.models.base import Base, SoftDeleteMixin, TimestampMixin
from sourcemind.models.connector import (
    ArtifactHead,
    ArtifactLink,
    ConnectorConfig,
    ConnectorSyncCursor,
    ConnectorSyncLog,
    ConnectorWorkItem,
    GitHubAuthorLink,
)
from sourcemind.models.document import Document, DocumentSourceType, IngestionStatus
from sourcemind.models.mcp_key import McpApiKey, McpKeyEvent
from sourcemind.models.memory import Memory
from sourcemind.models.memory_conflict import ConflictStatus, ConflictType, MemoryConflict
from sourcemind.models.memory_relation import MemoryRelation, RelationType
from sourcemind.models.organization import Organization, OrganizationPlan
from sourcemind.models.search_evidence import SearchEvent, SearchRating
from sourcemind.models.user import User
from sourcemind.models.workspace import (
    Workspace,
    WorkspaceMember,
    WorkspaceMembershipStatus,
    WorkspaceRole,
)
from sourcemind.models.workspace_invitation import WorkspaceInvitation

__all__ = [
    "Base",
    "TimestampMixin",
    "SoftDeleteMixin",
    "Organization",
    "OrganizationPlan",
    "Workspace",
    "WorkspaceMembershipStatus",
    "WorkspaceRole",
    "WorkspaceMember",
    "WorkspaceInvitation",
    "User",
    "Document",
    "DocumentSourceType",
    "IngestionStatus",
    "Memory",
    "McpApiKey",
    "McpKeyEvent",
    "Attribution",
    "AttributionEdit",
    "AttributionActionType",
    "MemoryRelation",
    "RelationType",
    "MemoryConflict",
    "ConflictType",
    "ConflictStatus",
    "ConnectorConfig",
    "ConnectorSyncLog",
    "ArtifactHead",
    "ArtifactLink",
    "ConnectorSyncCursor",
    "ConnectorWorkItem",
    "GitHubAuthorLink",
    "SearchEvent",
    "SearchRating",
]
