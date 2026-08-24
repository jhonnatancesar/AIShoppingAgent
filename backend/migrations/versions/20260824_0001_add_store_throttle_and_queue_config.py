"""Add global store throttle state and persisted queue config (TASK-108).

Revision ID: 20260824_0001
Revises: 20260822_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_0001"
down_revision: str | None = "20260822_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "store_throttle_state",
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("next_allowed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["store_id"],
            ["stores.id"],
            name=op.f("fk_store_throttle_state_store_id_stores"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("store_id", name=op.f("pk_store_throttle_state")),
    )
    op.create_table(
        "collection_queue_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "max_concurrent_user_batches_override", sa.Integer(), nullable=True
        ),
        sa.Column(
            "user_cooldown_min_seconds_override", sa.Float(), nullable=True
        ),
        sa.Column(
            "user_cooldown_max_seconds_override", sa.Float(), nullable=True
        ),
        sa.Column(
            "store_min_interval_seconds_override", sa.Float(), nullable=True
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "id = 1", name="ck_collection_queue_config_singleton"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_queue_config")),
    )


def downgrade() -> None:
    op.drop_table("collection_queue_config")
    op.drop_table("store_throttle_state")
