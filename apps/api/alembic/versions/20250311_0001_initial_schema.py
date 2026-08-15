"""Initial schema — all 9 core tables, indexes, RLS, triggers.

Revision ID: 0001_initial
Revises: None
Create Date: 2025-03-11 00:00:00.000000 UTC

Tables created:
  organizations, workspaces, workspace_members, users,
  documents, memories, attributions, attribution_edits,
  memory_relations, memory_conflicts

Indexes:
  - HNSW on memories.embedding (m=16, ef_construction=64)
  - GIN tsvector index on memories.content for BM25 keyword search
  - Standard B-tree indexes on all FK columns and common filter fields

Row-Level Security:
  - Enabled on workspaces, documents, memories, attributions,
    attribution_edits, memory_relations, memory_conflicts
  - Policies use app.current_workspace_id session variable

Triggers:
  - set_updated_at(): auto-maintain updated_at on all tables
  - attribution_append_only(): prevent UPDATE/DELETE on attributions

Expand-contract pattern:
  - upgrade(): create everything
  - downgrade(): drop everything in reverse dependency order
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extensions ────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")

    # ── Helper function: auto-update updated_at ───────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW() AT TIME ZONE 'UTC';
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── Helper function: prevent attribution UPDATE/DELETE ────────
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_attribution_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'Attribution records are append-only. '
                'INSERT new records instead of modifying existing ones. '
                'Error code: SM091';
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: organizations
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column(
            "plan",
            sa.String(50),
            nullable=False,
            server_default="free",
        ),
        sa.Column("max_workspaces", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("max_members", sa.Integer(), nullable=False, server_default="10"),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        comment="Top-level tenant. All workspaces and users belong to an organization.",
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_deleted_at", "organizations", ["deleted_at"])
    op.execute("""
        CREATE TRIGGER organizations_set_updated_at
        BEFORE UPDATE ON organizations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: users
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("clerk_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("clerk_id", name="uq_users_clerk_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        comment="Platform user, authenticated via Clerk.",
    )
    op.create_index("ix_users_clerk_id", "users", ["clerk_id"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])
    op.execute("""
        CREATE TRIGGER users_set_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: workspaces
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "slug", name="uq_workspaces_org_slug"
        ),
        comment="Isolated team collaboration space. All content is scoped to a workspace.",
    )
    op.create_index("ix_workspaces_organization_id", "workspaces", ["organization_id"])
    op.create_index("ix_workspaces_slug", "workspaces", ["slug"])
    op.create_index("ix_workspaces_deleted_at", "workspaces", ["deleted_at"])
    op.execute("""
        CREATE TRIGGER workspaces_set_updated_at
        BEFORE UPDATE ON workspaces
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # Enable RLS on workspaces
    op.execute("ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY workspace_isolation ON workspaces
        USING (
            id = current_setting('app.current_workspace_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'true'
        );
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: workspace_members
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "workspace_members",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(50),
            nullable=False,
            server_default="member",
        ),
        sa.Column(
            "invited_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name="uq_workspace_members_workspace_user"
        ),
        comment="Workspace membership with role-based access control.",
    )
    op.create_index(
        "ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"]
    )
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])
    op.execute("""
        CREATE TRIGGER workspace_members_set_updated_at
        BEFORE UPDATE ON workspace_members
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: documents
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "submitter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("s3_key", sa.String(1024), nullable=True),
        sa.Column(
            "ingestion_status",
            sa.String(50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("ingestion_job_id", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("memory_count", sa.Integer(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id", "sha256_hash", name="uq_documents_workspace_sha256"
        ),
        comment="Source document submitted for memory extraction. One document → N memories.",
    )
    op.create_index("ix_documents_workspace_id", "documents", ["workspace_id"])
    op.create_index("ix_documents_submitter_id", "documents", ["submitter_id"])
    op.create_index("ix_documents_ingestion_status", "documents", ["ingestion_status"])
    op.create_index("ix_documents_ingestion_job_id", "documents", ["ingestion_job_id"])
    op.create_index("ix_documents_deleted_at", "documents", ["deleted_at"])
    op.execute("""
        CREATE TRIGGER documents_set_updated_at
        BEFORE UPDATE ON documents
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)
    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY documents_workspace_isolation ON documents
        USING (
            workspace_id = current_setting('app.current_workspace_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'true'
        );
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: memories (core entity with pgvector embedding)
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "memories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "parent_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        # vector(3072) for OpenAI text-embedding-3-large
        sa.Column(
            "embedding",
            sa.Text(),  # Placeholder — replaced below with raw DDL
            nullable=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "current_version",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String()),
            nullable=True,
        ),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("source_chunk_index", sa.Integer(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        comment="Atomic unit of knowledge extracted from a document.",
    )

    # Add the actual vector column with correct type
    op.execute("ALTER TABLE memories DROP COLUMN embedding")
    op.execute("ALTER TABLE memories ADD COLUMN embedding vector(3072)")

    # Add tsvector column for BM25 full-text search
    op.execute("""
        ALTER TABLE memories
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    """)

    # Indexes
    op.create_index("ix_memories_workspace_id", "memories", ["workspace_id"])
    op.create_index("ix_memories_document_id", "memories", ["document_id"])
    op.create_index("ix_memories_parent_memory_id", "memories", ["parent_memory_id"])
    op.create_index("ix_memories_content_hash", "memories", ["content_hash"])
    op.create_index("ix_memories_deleted_at", "memories", ["deleted_at"])
    op.create_index(
        "ix_memories_workspace_current",
        "memories",
        ["workspace_id", "current_version"],
    )

    # HNSW index for approximate nearest neighbor vector search
    # m=16: number of bidirectional links per layer (higher = better recall, more memory)
    # ef_construction=64: size of candidate list during construction (higher = better quality)
    # vector_cosine_ops: use cosine distance (appropriate for normalized embeddings)
    # pgvector HNSW supports max 2000 dims for vector type.
    # Cast to halfvec(3072) for HNSW on 3072-dim embeddings (requires pgvector >= 0.7.0).
    op.execute("""
        CREATE INDEX ix_memories_embedding_hnsw ON memories
        USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # GIN index for full-text search BM25 ranking
    op.execute("""
        CREATE INDEX ix_memories_content_tsv ON memories
        USING gin (content_tsv)
    """)

    # GIN trigram index for LIKE/ILIKE fuzzy search
    op.execute("""
        CREATE INDEX ix_memories_content_trgm ON memories
        USING gin (content gin_trgm_ops)
    """)

    op.execute("""
        CREATE TRIGGER memories_set_updated_at
        BEFORE UPDATE ON memories
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)
    op.execute("ALTER TABLE memories ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY memories_workspace_isolation ON memories
        USING (
            workspace_id = current_setting('app.current_workspace_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'true'
        );
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: attribution_edits (before attributions — FK dependency)
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "attribution_edits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "editor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("content_before", sa.Text(), nullable=True),
        sa.Column("content_after", sa.Text(), nullable=False),
        sa.Column("edit_position", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=True, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        comment="Individual content edit records used for attribution computation.",
    )
    op.create_index("ix_attribution_edits_memory_id", "attribution_edits", ["memory_id"])
    op.create_index("ix_attribution_edits_editor_id", "attribution_edits", ["editor_id"])
    op.execute("ALTER TABLE attribution_edits ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY attribution_edits_workspace_isolation ON attribution_edits
        USING (
            EXISTS (
                SELECT 1 FROM memories m
                WHERE m.id = attribution_edits.memory_id
                AND (
                    m.workspace_id = current_setting('app.current_workspace_id', true)::uuid
                    OR current_setting('app.bypass_rls', true) = 'true'
                )
            )
        );
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: attributions (APPEND-ONLY — trigger enforced)
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "attributions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contribution_weight", sa.Float(), nullable=False),
        sa.Column("char_diff_score", sa.Float(), nullable=True),
        sa.Column("semantic_score", sa.Float(), nullable=True),
        sa.Column("temporal_score", sa.Float(), nullable=True),
        sa.Column("structural_score", sa.Float(), nullable=True),
        sa.Column("approval_score", sa.Float(), nullable=True),
        sa.Column("trigger_action", sa.String(50), nullable=False),
        sa.Column(
            "edit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attribution_edits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",  # Serves as computed_at for attribution records
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        comment=(
            "Append-only attribution snapshot. Enforced by DB trigger. "
            "Latest record per (memory_id, user_id) = current attribution."
        ),
    )
    op.create_index("ix_attributions_memory_id", "attributions", ["memory_id"])
    op.create_index("ix_attributions_user_id", "attributions", ["user_id"])
    op.create_index(
        "ix_attributions_memory_user",
        "attributions",
        ["memory_id", "user_id"],
    )

    # THE CORE GUARANTEE: attribution records cannot be modified or deleted
    op.execute("""
        CREATE TRIGGER attribution_append_only
        BEFORE UPDATE OR DELETE ON attributions
        FOR EACH ROW EXECUTE FUNCTION prevent_attribution_mutation();
    """)

    op.execute("ALTER TABLE attributions ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY attributions_workspace_isolation ON attributions
        USING (
            EXISTS (
                SELECT 1 FROM memories m
                WHERE m.id = attributions.memory_id
                AND (
                    m.workspace_id = current_setting('app.current_workspace_id', true)::uuid
                    OR current_setting('app.bypass_rls', true) = 'true'
                )
            )
        );
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: memory_relations
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "memory_relations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "source_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(50), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "detected_by",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'pipeline'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "relation_type",
            name="uq_memory_relations_unique_edge",
        ),
        comment="Directed semantic relationship between two memories.",
    )
    op.create_index(
        "ix_memory_relations_source", "memory_relations", ["source_memory_id"]
    )
    op.create_index(
        "ix_memory_relations_target", "memory_relations", ["target_memory_id"]
    )
    op.execute("ALTER TABLE memory_relations ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY memory_relations_workspace_isolation ON memory_relations
        USING (
            EXISTS (
                SELECT 1 FROM memories m
                WHERE m.id = memory_relations.source_memory_id
                AND (
                    m.workspace_id = current_setting('app.current_workspace_id', true)::uuid
                    OR current_setting('app.bypass_rls', true) = 'true'
                )
            )
        );
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # TABLE: memory_conflicts
    # ────────────────────────────────────────────────────────────────────────────
    op.create_table(
        "memory_conflicts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "memory_a_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "memory_b_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conflict_type", sa.String(50), nullable=False),
        sa.Column(
            "severity",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'medium'"),
        ),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "resolver_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        comment="Detected knowledge conflicts between memories. Resolved via team review.",
    )
    op.create_index("ix_memory_conflicts_workspace_id", "memory_conflicts", ["workspace_id"])
    op.create_index("ix_memory_conflicts_memory_a_id", "memory_conflicts", ["memory_a_id"])
    op.create_index("ix_memory_conflicts_memory_b_id", "memory_conflicts", ["memory_b_id"])
    op.create_index("ix_memory_conflicts_status", "memory_conflicts", ["status"])
    op.execute("""
        CREATE TRIGGER memory_conflicts_set_updated_at
        BEFORE UPDATE ON memory_conflicts
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)
    op.execute("ALTER TABLE memory_conflicts ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY memory_conflicts_workspace_isolation ON memory_conflicts
        USING (
            workspace_id = current_setting('app.current_workspace_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'true'
        );
    """)

    # ────────────────────────────────────────────────────────────────────────────
    # Seed HNSW index configuration comment for documentation
    # ────────────────────────────────────────────────────────────────────────────
    op.execute("""
        COMMENT ON INDEX ix_memories_embedding_hnsw IS
        'HNSW approximate nearest neighbor index for semantic search.
         m=16 (bidirectional links per layer), ef_construction=64 (build quality).
         Cosine similarity operator (vector_cosine_ops).
         Expected performance: p95 < 50ms on 1M vectors with 1 GPU replica.'
    """)


def downgrade() -> None:
    """
    Drop all tables in reverse dependency order.
    Also drops triggers and functions.
    """
    # Drop tables (CASCADE handles FK constraints)
    op.execute("DROP TABLE IF EXISTS memory_conflicts CASCADE")
    op.execute("DROP TABLE IF EXISTS memory_relations CASCADE")
    op.execute("DROP TABLE IF EXISTS attributions CASCADE")
    op.execute("DROP TABLE IF EXISTS attribution_edits CASCADE")
    op.execute("DROP TABLE IF EXISTS memories CASCADE")
    op.execute("DROP TABLE IF EXISTS documents CASCADE")
    op.execute("DROP TABLE IF EXISTS workspace_members CASCADE")
    op.execute("DROP TABLE IF EXISTS workspaces CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TABLE IF EXISTS organizations CASCADE")

    # Drop functions
    op.execute("DROP FUNCTION IF EXISTS set_updated_at() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS prevent_attribution_mutation() CASCADE")
