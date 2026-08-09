"""Adiciona marcadores idempotentes de notificação de sessão.

Revision ID: 20260809_0002
Revises: 20260809_0001
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0002"
down_revision: str | None = "20260809_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_auth_sessions",
        sa.Column(
            "expiry_warning_event_published",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "user_auth_sessions",
        sa.Column(
            "expiry_event_published",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    # Sessões anteriores à funcionalidade não geram mensagens retroativas.
    op.execute(
        sa.text(
            "UPDATE user_auth_sessions SET "
            "expiry_warning_event_published = true, "
            "expiry_event_published = true"
        )
    )
    op.create_index(
        "ix_user_auth_sessions_expiry_notifications",
        "user_auth_sessions",
        ["expires_at"],
        postgresql_where=sa.text(
            "revoked_at IS NULL AND "
            "(expiry_warning_event_published = false OR "
            "expiry_event_published = false)"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_auth_sessions_expiry_notifications",
        table_name="user_auth_sessions",
    )
    op.drop_column("user_auth_sessions", "expiry_event_published")
    op.drop_column("user_auth_sessions", "expiry_warning_event_published")
