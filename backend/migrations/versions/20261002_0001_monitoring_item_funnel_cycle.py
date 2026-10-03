"""TASK-136: ciclo de coleta por item de monitoramento (barreira antes da IA).

`funnel_closed_at` / `funnel_previous_closed_at` marcam o ciclo fechado: todas as
lojas habilitadas do item rodaram (com sucesso ou erro) e só então o funil escolhe
quem vai para a IA. Itens que já existem recebem os dois marcos em `now()`: coletas
anteriores ficam fora de qualquer ciclo e seu fan-out pendente segue sem funil
(comportamento da v1.4).

Revision ID: 20261002_0001
Revises: 20261001_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_0001"
down_revision: str | None = "20261001_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "monitoring_items"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("funnel_closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "funnel_previous_closed_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.execute(
        "UPDATE monitoring_items SET funnel_closed_at = now(), "
        "funnel_previous_closed_at = now()"
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "funnel_previous_closed_at")
    op.drop_column(_TABLE, "funnel_closed_at")
