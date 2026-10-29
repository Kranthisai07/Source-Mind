"""Response shapes for the graph-backed read endpoints.

Every response says which backend answered (``source``): the graph when it is enabled, reachable,
fresh and agrees with PostgreSQL about the memory in question, PostgreSQL otherwise. The two
backends return the same shape, and parity tests keep them returning the same answer.

Ids and numbers only, plus the display names and short previews that the caller can already see
through row-level security. Names and previews are filled in from PostgreSQL; the graph never
stores them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

GraphSource = Literal["graph", "postgres"]
RelationType = Literal["updates", "extends", "derives", "contradicts", "references"]


class Contributor(BaseModel):
    user_id: UUID
    display_name: str | None = None
    weight: float = Field(description="Latest snapshot. Weights are not normalised.")
    action: str | None = Field(default=None, description="Open string: create, edit, transfer...")


class LineageVersion(BaseModel):
    id: UUID
    version: int
    is_current: bool
    created_at: datetime | None = None
    contributors: list[Contributor] = Field(default_factory=list)


class LineageResponse(BaseModel):
    memory_id: UUID
    source: GraphSource
    versions: list[LineageVersion]


class RelatedNode(BaseModel):
    id: UUID
    version: int
    is_current: bool
    importance: float
    hops: int = Field(description="0 for the memory itself")
    preview: str = ""


class RelatedEdge(BaseModel):
    id: UUID
    source: UUID
    target: UUID
    type: str
    confidence: float | None = None


class RelatedResponse(BaseModel):
    memory_id: UUID
    source: GraphSource
    depth_requested: int
    depth_served: int = Field(description="Below depth_requested when PostgreSQL answered")
    nodes: list[RelatedNode]
    edges: list[RelatedEdge]
    truncated: bool = False


class OwnerEntry(BaseModel):
    user_id: UUID
    display_name: str | None = None
    score: float
    direct_weight: float | None = Field(
        default=None, description="Weight on this memory itself, when they contributed to it"
    )
    hops: int = Field(
        description="Distance from this memory to the nearest one they contributed to"
    )
    path: list[UUID] = Field(description="Memory ids from this memory to that one")


class OwnersResponse(BaseModel):
    memory_id: UUID
    source: GraphSource
    depth_served: int
    owners: list[OwnerEntry]


class CollaborationNode(BaseModel):
    user_id: UUID
    display_name: str | None = None
    memory_count: int


class CollaborationEdge(BaseModel):
    source: UUID
    target: UUID
    shared_memories: int


class CollaborationResponse(BaseModel):
    source: GraphSource
    nodes: list[CollaborationNode]
    edges: list[CollaborationEdge]
    truncated: bool = False
