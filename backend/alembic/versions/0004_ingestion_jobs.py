"""Persist ingestion work for a single independent worker.

Revision ID: 0004_ingestion_jobs
Revises: 0003_ingest_vectors
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0004_ingestion_jobs"
down_revision = "0003_ingest_vectors"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("build_id", UUID(as_uuid=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("profile", JSONB(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_summary", sa.String(200)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["document_id", "build_id"],
            ["document_builds.document_id", "document_builds.id"],
            name="fk_ingestion_jobs_build_same_document",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_ingestion_jobs_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_ingestion_jobs_attempts"),
        sa.CheckConstraint(
            "status <> 'failed' OR "
            "(error_code IS NOT NULL AND error_summary IS NOT NULL)",
            name="ck_ingestion_jobs_failed_error",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR build_id IS NOT NULL",
            name="ck_ingestion_jobs_success_build",
        ),
    )
    op.create_index(
        "uq_ingestion_jobs_active_document",
        "ingestion_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_ingestion_jobs_queue", "ingestion_jobs", ["status", "created_at", "id"]
    )
    op.create_index(
        "ix_ingestion_jobs_document_created",
        "ingestion_jobs",
        ["document_id", "created_at"],
    )


def downgrade():
    op.drop_table("ingestion_jobs")
