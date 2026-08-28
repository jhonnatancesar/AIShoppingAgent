"""Escopa store_activity_state por unidade de monitoramento, não só loja (TASK-116).

Revision ID: 20260828_0001
Revises: 20260827_0001

Achado real em PROD (auditoria pós-deploy v1.2.2): HIGH_ACTIVITY era
detectado POR LOJA inteira -- uma mudança de preço em qualquer produto
de qualquer Mission na mesma loja acelerava a cadência de TODOS os
outros itens monitorados naquela loja, mesmo produtos totalmente
alheios. Dado o histórico real de bloqueio anti-bot (Terabyte/
Cloudflare, DEC-070), esse acoplamento é um risco operacional real, não
só teórico.

`store_activity_state` passa a ser chaveada por `(store_id, scope_id)`,
onde `scope_id` é `MonitoringItem.id` (caminho compartilhado TASK-112)
ou `Mission.id` (caminho legado sem `MonitoringItem`).

A tabela guarda só HISTERESE recomputável a partir de `PriceObservation`
(nunca fonte de verdade -- ver docstring de `StoreActivityState`) --
seguro limpar as poucas linhas existentes; a próxima avaliação de
cadência recalcula do zero, sem perda de dado real.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260828_0001"
down_revision = "20260827_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM store_activity_state")
    op.drop_constraint(
        "pk_store_activity_state", "store_activity_state", type_="primary"
    )
    op.add_column(
        "store_activity_state",
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_primary_key(
        "pk_store_activity_state",
        "store_activity_state",
        ["store_id", "scope_id"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM store_activity_state")
    op.drop_constraint(
        "pk_store_activity_state", "store_activity_state", type_="primary"
    )
    op.drop_column("store_activity_state", "scope_id")
    op.create_primary_key(
        "pk_store_activity_state", "store_activity_state", ["store_id"]
    )
