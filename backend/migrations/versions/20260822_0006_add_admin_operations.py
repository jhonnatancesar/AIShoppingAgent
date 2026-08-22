"""Admin lifecycle and disabled API-key foundation (TASK-102).

Revision ID: 20260822_0006
Revises: 20260822_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0006"
down_revision: str | None = "20260822_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "lifecycle_status", sa.String(16), server_default="active", nullable=False
        ),
    )
    op.add_column(
        "users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "user_lifecycle_status_values",
        "users",
        "lifecycle_status IN ('active', 'inactive', 'blocked', 'deleted')",
    )
    op.create_table(
        "admin_api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.String(80)),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "btrim(name) <> ''", name="ck_admin_api_keys_name_not_blank"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_prefix"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_admin_api_keys_owner", "admin_api_keys", ["owner_user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("admin_api_keys")
    op.drop_constraint("user_lifecycle_status_values", "users", type_="check")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "lifecycle_status")
