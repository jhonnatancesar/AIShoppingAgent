"""TASK-136: vendas publicadas pela loja no card, sinal de popularidade lido
antes da IA (Amazon: "Mais de 1 mil comprados no mês passado", escopo
`last_month`; Mercado Livre: "+500 vendidos", escopo `total`). Colunas só na
Offer, todas nulas até a loja informar; o trio é todo preenchido ou todo nulo.

Revision ID: 20261001_0001
Revises: 20260929_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261001_0001"
down_revision: str | None = "20260929_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "offers"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("sales_count", sa.BigInteger(), nullable=True))
    op.add_column(_TABLE, sa.Column("sales_scope", sa.String(length=16), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("sales_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_offers_sales_count_non_negative",
        _TABLE,
        "sales_count IS NULL OR sales_count >= 0",
    )
    op.create_check_constraint(
        "ck_offers_sales_scope_values",
        _TABLE,
        "sales_scope IS NULL OR sales_scope IN ('last_month', 'total')",
    )
    op.create_check_constraint(
        "ck_offers_sales_snapshot_complete",
        _TABLE,
        "(sales_count IS NULL AND sales_scope IS NULL AND sales_observed_at IS NULL) OR "
        "(sales_count IS NOT NULL AND sales_scope IS NOT NULL AND "
        "sales_observed_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_offers_sales_snapshot_complete", _TABLE, type_="check")
    op.drop_constraint("ck_offers_sales_scope_values", _TABLE, type_="check")
    op.drop_constraint("ck_offers_sales_count_non_negative", _TABLE, type_="check")
    op.drop_column(_TABLE, "sales_observed_at")
    op.drop_column(_TABLE, "sales_scope")
    op.drop_column(_TABLE, "sales_count")
