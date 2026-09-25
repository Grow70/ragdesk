"""Add 1536-dimensional vectors and traceable ingestion configuration.

Revision ID: 0003_ingest_vectors
Revises: 0002_user_credentials
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0003_ingest_vectors"
down_revision = "0002_user_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("document_builds", sa.Column("embedding_provider", sa.String(32)))
    op.add_column("document_builds", sa.Column("embedding_model", sa.String(100)))
    op.add_column("document_builds", sa.Column("embedding_dimensions", sa.Integer()))
    op.add_column("document_builds", sa.Column("config_version", sa.String(50)))
    op.add_column("document_builds", sa.Column("expected_chunk_count", sa.Integer()))
    op.create_check_constraint(
        "ck_document_builds_embedding_dimensions",
        "document_builds",
        "embedding_dimensions IS NULL OR embedding_dimensions > 0",
    )
    op.create_check_constraint(
        "ck_document_builds_expected_chunks",
        "document_builds",
        "expected_chunk_count IS NULL OR expected_chunk_count > 0",
    )
    op.add_column("chunks", sa.Column("source_spans", JSONB()))
    op.add_column("chunks", sa.Column("embedding", VECTOR(1536)))


def downgrade() -> None:
    op.drop_column("chunks", "embedding")
    op.drop_column("chunks", "source_spans")
    op.drop_constraint(
        "ck_document_builds_expected_chunks", "document_builds", type_="check"
    )
    op.drop_constraint(
        "ck_document_builds_embedding_dimensions", "document_builds", type_="check"
    )
    op.drop_column("document_builds", "expected_chunk_count")
    op.drop_column("document_builds", "config_version")
    op.drop_column("document_builds", "embedding_dimensions")
    op.drop_column("document_builds", "embedding_model")
    op.drop_column("document_builds", "embedding_provider")
