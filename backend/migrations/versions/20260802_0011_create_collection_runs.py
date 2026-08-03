"""Cria execuções persistentes de coleta. Revision ID: 20260802_0011 Revises: 20260802_0010"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260802_0011"
down_revision = "20260802_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = postgresql.ENUM(
        "running",
        "succeeded",
        "failed",
        name="collection_run_status",
        create_type=False,
    )
    status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "collection_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", status, server_default="running", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "(status = 'running' AND finished_at IS NULL) OR (status IN ('succeeded', 'failed') AND finished_at IS NOT NULL)",
            name="ck_collection_runs_terminal_finished",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_collection_runs_time_order",
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_collection_runs_mission_started_at",
        "collection_runs",
        ["mission_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_collection_runs_store_started_at",
        "collection_runs",
        ["store_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_collection_runs_store_started_at", table_name="collection_runs")
    op.drop_index("ix_collection_runs_mission_started_at", table_name="collection_runs")
    op.drop_table("collection_runs")
    postgresql.ENUM(name="collection_run_status").drop(op.get_bind(), checkfirst=True)
