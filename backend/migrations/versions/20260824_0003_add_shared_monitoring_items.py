"""Add shared monitoring items, links and per-store need (TASK-112 fase 2).

Revision ID: 20260824_0003
Revises: 20260824_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0003"
down_revision: str | None = "20260824_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitoring_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monitoring_key", sa.String(length=160), nullable=False),
        sa.Column("identity_version", sa.Integer(), nullable=False),
        sa.Column("canonical_identity", postgresql.JSONB(), nullable=False),
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
            "identity_version > 0",
            name="ck_monitoring_items_identity_version_positive",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_monitoring_items")),
    )
    op.create_index(
        "uq_monitoring_items_monitoring_key",
        "monitoring_items",
        ["monitoring_key"],
        unique=True,
    )

    op.create_table(
        "mission_monitoring_items",
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monitoring_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name=op.f("fk_mission_monitoring_items_mission_id_missions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["monitoring_item_id"],
            ["monitoring_items.id"],
            name=op.f(
                "fk_mission_monitoring_items_monitoring_item_id_monitoring_items"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("mission_id", name=op.f("pk_mission_monitoring_items")),
    )
    op.create_index(
        op.f("ix_mission_monitoring_items_monitoring_item_id"),
        "mission_monitoring_items",
        ["monitoring_item_id"],
    )

    op.create_table(
        "monitoring_item_stores",
        sa.Column("monitoring_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "is_enabled", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_blocks", sa.Integer(), server_default="0", nullable=False
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
            "consecutive_blocks >= 0",
            name="ck_monitoring_item_stores_consecutive_blocks_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["monitoring_item_id"],
            ["monitoring_items.id"],
            name=op.f(
                "fk_monitoring_item_stores_monitoring_item_id_monitoring_items"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_monitoring_item_stores_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "monitoring_item_id", "store_id", name=op.f("pk_monitoring_item_stores")
        ),
    )


def downgrade() -> None:
    op.drop_table("monitoring_item_stores")
    op.drop_index(
        op.f("ix_mission_monitoring_items_monitoring_item_id"),
        table_name="mission_monitoring_items",
    )
    op.drop_table("mission_monitoring_items")
    op.drop_index("uq_monitoring_items_monitoring_key", table_name="monitoring_items")
    op.drop_table("monitoring_items")
