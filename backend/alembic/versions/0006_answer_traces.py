"""Store redacted request traces without external monitoring infrastructure."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0006_answer_traces"
down_revision = "0005_job_leases"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "answer_traces",
        sa.Column("request_id", sa.String(32), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True)),
        sa.Column("knowledge_base_id", UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
    )
    op.create_index(
        "ix_answer_traces_kb_created",
        "answer_traces",
        ["knowledge_base_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_answer_traces_kb_created", table_name="answer_traces")
    op.drop_table("answer_traces")
