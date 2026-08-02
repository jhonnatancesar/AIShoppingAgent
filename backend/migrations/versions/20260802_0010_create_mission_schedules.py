"""Cria agendas recorrentes de missão.

Revision ID: 20260802_0010
Revises: 20260802_0009
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260802_0010"
down_revision: str | None = "20260802_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Cria uma agenda editável por missão."""
    op.create_table(
        "mission_schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
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
            "interval_minutes > 0",
            name="ck_mission_schedules_interval_positive",
        ),
        sa.CheckConstraint(
            "last_run_at IS NULL OR last_run_at <= next_run_at",
            name="ck_mission_schedules_run_order",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_schedules_mission_id_missions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_schedules")),
        sa.UniqueConstraint("mission_id", name=op.f("uq_mission_schedules_mission_id")),
    )
    op.create_index(
        "ix_mission_schedules_due",
        "mission_schedules",
        ["next_run_at", "mission_id"],
        postgresql_where=sa.text("is_enabled"),
    )


def downgrade() -> None:
    """Remove somente as agendas introduzidas nesta revisão."""
    op.drop_index("ix_mission_schedules_due", table_name="mission_schedules")
    op.drop_table("mission_schedules")
