"""Adiciona backoff persistente por (mission_id, store_id) em mission_sources.

Revision ID: 20260809_0003
Revises: 20260809_0002
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0003"
down_revision: str | None = "20260809_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mission_sources",
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mission_sources",
        sa.Column(
            "consecutive_blocks",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_mission_sources_consecutive_blocks_non_negative",
        "mission_sources",
        "consecutive_blocks >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mission_sources_consecutive_blocks_non_negative",
        "mission_sources",
        type_="check",
    )
    op.drop_column("mission_sources", "consecutive_blocks")
    op.drop_column("mission_sources", "next_eligible_at")
