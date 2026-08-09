"""Adiciona garantias concorrentes da orquestração de coleta (TASK-062).

Revision ID: 20260809_0001
Revises: 20260808_0009
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260809_0001"
down_revision: str | None = "20260808_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_collection_runs_running_mission_store",
        "collection_runs",
        ["mission_id", "store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND mission_id IS NOT NULL"),
    )
    op.create_index(
        "uq_price_observations_run_offer",
        "price_observations",
        ["collection_run_id", "offer_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_price_observations_run_offer", table_name="price_observations")
    op.drop_index(
        "uq_collection_runs_running_mission_store", table_name="collection_runs"
    )
