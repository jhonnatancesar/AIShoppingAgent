"""TASK-128: o cache de títulos (`product_identity_candidates`) passa a
guardar também os resultados em que a IA NÃO fecha uma identidade exata
-- decisão do usuário (2026-09-25/26): "ela não pode não resolver, ela
sempre vai ter que criar um vínculo de alguma coisa".

Antes, uma extração sem marca/família/modelo não gravava nada: o mesmo
título chamava a IA de novo a cada ciclo de coleta, para sempre. Dois
estados novos:

- `partial`: vínculo parcial (categoria + marca/família quando aparecem
  no título) -- nunca carrega `identity_key`/`family_key`, então nunca
  funde produtos diferentes (`uq_products_identity_key`).
- `awaiting_page`: nem a categoria saiu do título -- o worker vai ler a
  página do produto e a IA tenta de novo com esse contexto (etapa 2).

Os campos de identidade passam a aceitar NULL só para esses dois
estados; `approved`/`pending_review`/`rejected` continuam exigindo tudo
preenchido, exatamente como antes (CHECK condicional).

Revision ID: 20260926_0001
Revises: 20260913_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_0001"
down_revision: str | None = "20260913_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "product_identity_candidates"
_IDENTITY_COLUMNS = (
    ("category", 80),
    ("brand", 160),
    ("family", 160),
    ("model", 160),
    ("variant", 160),
    ("family_key", 80),
    ("identity_key", 80),
)
_OLD_STATUSES = "'approved', 'pending_review', 'rejected'"
_NEW_STATUSES = "'approved', 'pending_review', 'rejected', 'partial', 'awaiting_page'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_product_identity_candidates_status_values", _TABLE, type_="check"
    )
    op.create_check_constraint(
        "ck_product_identity_candidates_status_values",
        _TABLE,
        f"status IN ({_NEW_STATUSES})",
    )
    for column, length in _IDENTITY_COLUMNS:
        op.alter_column(
            _TABLE,
            column,
            existing_type=sa.String(length=length),
            nullable=True,
        )
    op.create_check_constraint(
        "ck_product_identity_candidates_complete_identity",
        _TABLE,
        f"status NOT IN ({_OLD_STATUSES}) OR ("
        "category IS NOT NULL AND brand IS NOT NULL AND family IS NOT NULL "
        "AND model IS NOT NULL AND variant IS NOT NULL "
        "AND family_key IS NOT NULL AND identity_key IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_product_identity_candidates_partial_shape",
        _TABLE,
        "status <> 'partial' OR (category IS NOT NULL AND model IS NULL "
        "AND family_key IS NULL AND identity_key IS NULL)",
    )
    op.create_check_constraint(
        "ck_product_identity_candidates_awaiting_page_shape",
        _TABLE,
        "status <> 'awaiting_page' OR (category IS NULL AND identity_key IS NULL)",
    )


def downgrade() -> None:
    # Só linhas de cache (sem identidade) -- nenhuma cabe de volta nas
    # colunas NOT NULL; apagá-las só faz a IA ser consultada de novo.
    op.execute(f"DELETE FROM {_TABLE} WHERE status IN ('partial', 'awaiting_page')")
    op.drop_constraint(
        "ck_product_identity_candidates_awaiting_page_shape", _TABLE, type_="check"
    )
    op.drop_constraint(
        "ck_product_identity_candidates_partial_shape", _TABLE, type_="check"
    )
    op.drop_constraint(
        "ck_product_identity_candidates_complete_identity", _TABLE, type_="check"
    )
    for column, length in _IDENTITY_COLUMNS:
        op.alter_column(
            _TABLE,
            column,
            existing_type=sa.String(length=length),
            nullable=False,
        )
    op.drop_constraint(
        "ck_product_identity_candidates_status_values", _TABLE, type_="check"
    )
    op.create_check_constraint(
        "ck_product_identity_candidates_status_values",
        _TABLE,
        f"status IN ({_OLD_STATUSES})",
    )
