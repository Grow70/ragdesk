"""Create the six core tables without an embedding column.

Revision ID: 0001_core_schema
Revises:
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_core_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "kb_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_kb_members_user"),
        sa.ForeignKeyConstraint(
            ["kb_id"], ["knowledge_bases.id"], name="fk_kb_members_kb"
        ),
        sa.UniqueConstraint("user_id", "kb_id", name="uq_kb_members_user_kb"),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_kb_members_role"),
    )
    op.create_index("ix_kb_members_kb_id", "kb_members", ["kb_id"])

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("active_build_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"], ["knowledge_bases.id"], name="fk_documents_kb"
        ),
        sa.CheckConstraint("length(file_sha256) = 64", name="ck_documents_hash_length"),
    )
    op.create_index("ix_documents_kb_deleted", "documents", ["kb_id", "deleted_at"])
    op.create_index("ix_documents_active_build_id", "documents", ["active_build_id"])
    op.create_index(
        "uq_documents_live_hash",
        "documents",
        ["kb_id", "file_sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "document_builds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("parser_config", postgresql.JSONB(), nullable=False),
        sa.Column("chunking_config", postgresql.JSONB(), nullable=False),
        sa.Column("model_config_id", sa.String(200), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_document_builds_document"
        ),
        sa.UniqueConstraint("document_id", "id", name="uq_document_builds_document_id"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed')",
            name="ck_document_builds_status",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL",
            name="ck_document_builds_failed_error",
        ),
    )
    op.create_index(
        "ix_document_builds_document_created",
        "document_builds",
        ["document_id", "created_at"],
    )
    op.create_foreign_key(
        "fk_documents_active_build_same_document",
        "documents",
        "document_builds",
        ["id", "active_build_id"],
        ["document_id", "id"],
    )

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("build_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("heading_path", postgresql.JSONB()),
        sa.Column("start_line", sa.Integer()),
        sa.Column("end_line", sa.Integer()),
        sa.ForeignKeyConstraint(
            ["build_id"], ["document_builds.id"], name="fk_chunks_build"
        ),
        sa.UniqueConstraint("build_id", "ordinal", name="uq_chunks_build_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal"),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_chunks_hash_length"),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number > 0", name="ck_chunks_page_number"
        ),
        sa.CheckConstraint(
            "start_line IS NULL OR start_line > 0", name="ck_chunks_start_line"
        ),
        sa.CheckConstraint(
            "end_line IS NULL OR (start_line IS NOT NULL AND end_line >= start_line)",
            name="ck_chunks_end_line",
        ),
        sa.CheckConstraint(
            "page_number IS NOT NULL OR start_line IS NOT NULL "
            "OR (heading_path IS NOT NULL AND jsonb_array_length(heading_path) > 0)",
            name="ck_chunks_locator",
        ),
        sa.CheckConstraint(
            "heading_path IS NULL OR jsonb_typeof(heading_path) = 'array'",
            name="ck_chunks_heading_path",
        ),
    )


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_constraint(
        "fk_documents_active_build_same_document", "documents", type_="foreignkey"
    )
    op.drop_index("ix_document_builds_document_created", table_name="document_builds")
    op.drop_table("document_builds")
    op.drop_index("uq_documents_live_hash", table_name="documents")
    op.drop_index("ix_documents_active_build_id", table_name="documents")
    op.drop_index("ix_documents_kb_deleted", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_kb_members_kb_id", table_name="kb_members")
    op.drop_table("kb_members")
    op.drop_table("knowledge_bases")
    op.drop_table("users")
