"""Adiciona autenticação persistente por senha (TASK-061).

Revision ID: 20260808_0008
Revises: 20260808_0007
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808_0008"
down_revision: str | None = "20260808_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column(
            "failed_login_attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("login_window_started_at", sa.DateTime(timezone=True)),
        sa.Column("login_locked_until", sa.DateTime(timezone=True)),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "failed_login_attempts >= 0", name="ck_user_credentials_failures"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "user_auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at = authenticated_at + INTERVAL '12 hours'",
            name="ck_user_auth_sessions_absolute_ttl",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= authenticated_at",
            name="ck_user_auth_sessions_revocation_time",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_auth_sessions_active_lookup",
        "user_auth_sessions",
        ["user_id", "telegram_user_id", "expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "credential_action_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("invalidated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('set_password', 'change_password', 'login', 'recover_password')",
            name="credential_action_values",
        ),
        sa.CheckConstraint(
            "expires_at = created_at + INTERVAL '10 minutes'",
            name="ck_credential_action_tokens_ttl",
        ),
        sa.CheckConstraint(
            "failed_attempts BETWEEN 0 AND 5",
            name="ck_credential_action_tokens_failures",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR invalidated_at IS NULL",
            name="ck_credential_action_tokens_single_terminal",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_credential_action_tokens_issuance",
        "credential_action_tokens",
        ["user_id", "action", "created_at"],
    )
    op.create_index(
        "ix_credential_action_tokens_expiry", "credential_action_tokens", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("credential_action_tokens")
    op.drop_table("user_auth_sessions")
    op.drop_table("user_credentials")
