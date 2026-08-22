"""Add temporary single-use Telegram link tokens (TASK-101).

Revision ID: 20260822_0005
Revises: 20260822_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0005"
down_revision: str | None = "20260822_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_link_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at = created_at + INTERVAL '10 minutes'",
            name="ck_telegram_link_tokens_ttl",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR invalidated_at IS NULL",
            name="ck_telegram_link_tokens_single_terminal",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_link_tokens_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_link_tokens")),
        sa.UniqueConstraint("token_hash", name="uq_telegram_link_tokens_token_hash"),
    )
    op.create_index(
        "ix_telegram_link_tokens_user_active",
        "telegram_link_tokens",
        ["user_id", "expires_at"],
        postgresql_where=sa.text(
            "consumed_at IS NULL AND invalidated_at IS NULL"
        ),
    )
    op.create_index(
        "ix_telegram_link_tokens_expiry",
        "telegram_link_tokens",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_link_tokens_expiry", table_name="telegram_link_tokens")
    op.drop_index(
        "ix_telegram_link_tokens_user_active", table_name="telegram_link_tokens"
    )
    op.drop_table("telegram_link_tokens")
