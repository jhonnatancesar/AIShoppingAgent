"""Cria critérios de missão.

Revision ID: 20260802_0007
Revises: 20260802_0006
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0007"
down_revision: str | None = "20260802_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cria um conjunto editável de critérios por missão."""
    op.create_table(
        "mission_criteria",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("search_query", sa.Text(), nullable=False),
        sa.Column("target_amount", sa.Numeric(precision=19, scale=4), nullable=True),
        sa.Column("target_currency", sa.CHAR(length=3), nullable=True),
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
            "btrim(search_query) <> ''",
            name="ck_mission_criteria_search_query_not_blank",
        ),
        sa.CheckConstraint(
            "target_amount IS NULL OR target_amount >= 0",
            name="ck_mission_criteria_target_amount_non_negative",
        ),
        sa.CheckConstraint(
            "(target_amount IS NULL AND target_currency IS NULL) OR "
            "(target_amount IS NOT NULL AND target_currency IS NOT NULL)",
            name="ck_mission_criteria_target_pair",
        ),
        sa.CheckConstraint(
            "target_currency IS NULL OR target_currency ~ '^[A-Z]{3}$'",
            name="ck_mission_criteria_currency_iso4217",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_criteria_mission_id_missions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_criteria")),
        sa.UniqueConstraint(
            "mission_id",
            name=op.f("uq_mission_criteria_mission_id"),
        ),
    )


def downgrade() -> None:
    """Remove somente os critérios introduzidos por esta revisão."""
    op.drop_table("mission_criteria")
