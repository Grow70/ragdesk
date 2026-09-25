"""Add optional login credentials to existing users.

Revision ID: 0002_user_credentials
Revises: 0001_core_schema
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_user_credentials"
down_revision = "0001_core_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("login_name", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "users", sa.Column("password_hash", sa.String(length=255), nullable=True)
    )
    op.create_unique_constraint("uq_users_login_name", "users", ["login_name"])
    op.create_check_constraint(
        "ck_users_password_hash_argon2id",
        "users",
        "password_hash IS NULL OR password_hash LIKE '$argon2id$%'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_password_hash_argon2id", "users", type_="check")
    op.drop_constraint("uq_users_login_name", "users", type_="unique")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "login_name")
