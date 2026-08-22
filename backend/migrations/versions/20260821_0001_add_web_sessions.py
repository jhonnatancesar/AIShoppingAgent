"""Add web_sessions table (TASK-091, item 1 da V1.2).

Revision ID: 20260821_0001
Revises: 20260817_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260821_0001"
down_revision: str | None = "20260817_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at = authenticated_at + INTERVAL '12 hours'",
            name="ck_web_sessions_absolute_ttl",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= authenticated_at",
            name="ck_web_sessions_revocation_time",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_web_sessions_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_sessions")),
        sa.UniqueConstraint("token_hash", name="uq_web_sessions_token_hash"),
    )
    op.create_index(
        "ix_web_sessions_active_lookup",
        "web_sessions",
        ["token_hash", "expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_web_sessions_user_lookup",
        "web_sessions",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_web_sessions_user_lookup", table_name="web_sessions")
    op.drop_index("ix_web_sessions_active_lookup", table_name="web_sessions")
    op.drop_table("web_sessions")
