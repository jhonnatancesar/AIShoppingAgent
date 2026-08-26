"""Add monitoring_item_id to collection_runs for shared collection (TASK-112 fase 3A).

Revision ID: 20260825_0001
Revises: 20260824_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260825_0001"
down_revision: str | None = "20260824_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_runs",
        sa.Column("monitoring_item_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_collection_runs_monitoring_item_id_monitoring_items"),
        "collection_runs",
        "monitoring_items",
        ["monitoring_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # Espelha uq_collection_runs_running_mission_store: no máximo uma
    # execução RUNNING por (monitoring_item_id, store_id) -- mesmo
    # mecanismo de claim/lock persistente já usado por missão, agora para
    # a necessidade compartilhada (nunca mutex em memória).
    op.create_index(
        "uq_collection_runs_running_monitoring_item_store",
        "collection_runs",
        ["monitoring_item_id", "store_id"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'running' AND monitoring_item_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_collection_runs_running_monitoring_item_store",
        table_name="collection_runs",
    )
    op.drop_constraint(
        op.f("fk_collection_runs_monitoring_item_id_monitoring_items"),
        "collection_runs",
        type_="foreignkey",
    )
    op.drop_column("collection_runs", "monitoring_item_id")
