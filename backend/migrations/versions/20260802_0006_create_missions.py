"""Cria missões persistentes.

Revision ID: 20260802_0006
Revises: 20260802_0005
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0006"
down_revision: str | None = "20260802_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MISSION_STATUS_VALUES = (
    "draft",
    "active",
    "paused",
    "completed",
    "cancelled",
    "expired",
)
mission_status = postgresql.ENUM(
    *MISSION_STATUS_VALUES,
    name="mission_status",
    create_type=False,
)


def upgrade() -> None:
    """Cria o estado atual das missões sem antecipar suas transições."""
    mission_status.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "missions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            mission_status,
            server_default=sa.text("'draft'::mission_status"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "state_version",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(title) <> ''",
            name="ck_missions_title_not_blank",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_missions_expiration_after_creation",
        ),
        sa.CheckConstraint(
            "state_version >= 0",
            name="ck_missions_state_version_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_missions_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_missions")),
    )
    op.create_index(
        "ix_missions_user_status_created_at",
        "missions",
        ["user_id", "status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_missions_pending_expiration",
        "missions",
        ["status", "expires_at"],
        postgresql_where=sa.text(
            "expires_at IS NOT NULL AND "
            "status NOT IN ('completed', 'cancelled', 'expired')"
        ),
    )


def downgrade() -> None:
    """Remove missões e depois seu tipo exclusivo."""
    op.drop_index("ix_missions_pending_expiration", table_name="missions")
    op.drop_index("ix_missions_user_status_created_at", table_name="missions")
    op.drop_table("missions")
    mission_status.drop(op.get_bind(), checkfirst=False)
