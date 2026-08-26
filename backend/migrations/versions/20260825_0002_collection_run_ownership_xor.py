"""Enforce mission_id XOR monitoring_item_id ownership on collection_runs (TASK-112 fase 3A).

Revision ID: 20260825_0002
Revises: 20260825_0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260825_0002"
down_revision: str | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Toda CollectionRun já criada até aqui (caminho por missão, sempre
    # via `start_collection_run`/construção direta) sempre preenche
    # `mission_id` e nunca `monitoring_item_id` -- auditoria confirmou
    # zero exceção em produção/testes; CHECK aditivo, sem backfill.
    op.create_check_constraint(
        "ck_collection_runs_ownership_xor",
        "collection_runs",
        "(mission_id IS NOT NULL AND monitoring_item_id IS NULL) OR "
        "(mission_id IS NULL AND monitoring_item_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_collection_runs_ownership_xor", "collection_runs", type_="check")
