"""
SQLAlchemy ORM models for SourceMind.

Import order matters for Alembic autogenerate — all models must be imported
here so that Base.metadata has full visibility during migration generation.
"""

from sourcemind.models.base import Base, TimestampMixin, SoftDeleteMixin
from sourcemind.models.organization import Organization, OrganizationPlan
from sourcemind.models.workspace import Workspace, WorkspaceRole, WorkspaceMember
from sourcemind.models.user import User
from sourcemind.models.document import Document, DocumentSourceType, IngestionStatus
from sourcemind.models.memory import Memory
from sourcemind.models.attribution import (
    Attribution,
    AttributionEdit,
    AttributionActionType,
)
from sourcemind.models.memory_relation import MemoryRelation, RelationType
from sourcemind.models.memory_conflict import MemoryConflict, ConflictType, ConflictStatus

__all__ = [
    "Base",
    "TimestampMixin",
    "SoftDeleteMixin",
    "Organization",
    "OrganizationPlan",
    "Workspace",
    "WorkspaceRole",
    "WorkspaceMember",
    "User",
    "Document",
    "DocumentSourceType",
    "IngestionStatus",
    "Memory",
    "Attribution",
    "AttributionEdit",
    "AttributionActionType",
    "MemoryRelation",
    "RelationType",
    "MemoryConflict",
    "ConflictType",
    "ConflictStatus",
]
