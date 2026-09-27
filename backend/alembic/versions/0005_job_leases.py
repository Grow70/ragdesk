"""Add leases and bounded attempts; stop old workers before upgrading.

Revision ID: 0005_job_leases
Revises: 0004_ingestion_jobs
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0005_job_leases"
down_revision = "0004_ingestion_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ingestion_jobs",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "ingestion_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "ingestion_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True))
    )
    op.add_column("ingestion_jobs", sa.Column("run_token", UUID(as_uuid=True)))
    op.create_check_constraint(
        "ck_ingestion_jobs_max_attempts",
        "ingestion_jobs",
        "max_attempts BETWEEN 1 AND 3",
    )


def downgrade():
    op.drop_constraint(
        "ck_ingestion_jobs_max_attempts", "ingestion_jobs", type_="check"
    )
    for column in ("run_token", "heartbeat_at", "lease_expires_at", "max_attempts"):
        op.drop_column("ingestion_jobs", column)
