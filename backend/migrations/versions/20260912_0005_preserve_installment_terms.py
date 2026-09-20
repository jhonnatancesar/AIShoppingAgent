"""Preserva condições distintas com a mesma quantidade de parcelas.

Revisão 20260912_0005, anterior 20260912_0004. Sem backfill de modalidade.
Downgrade recusa contagens duplicadas em vez de apagar histórico válido.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260912_0005"
down_revision = "20260912_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "offer_installment_options",
        sa.Column("payment_method", sa.String(100), nullable=True),
    )
    op.drop_constraint(
        "uq_offer_installment_options_observation_count",
        "offer_installment_options",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_offer_installment_options_terms",
        "offer_installment_options",
        [
            "price_observation_id",
            "installment_count",
            "installment_amount",
            "installment_total_amount",
            "discount_percent",
            "interest_kind",
            "payment_method",
        ],
        postgresql_nulls_not_distinct=True,
    )


def downgrade():
    # PostgreSQL valida antes de permitir a troca. Não colapsar opções.
    op.create_unique_constraint(
        "uq_offer_installment_options_observation_count",
        "offer_installment_options",
        ["price_observation_id", "installment_count"],
    )
    op.drop_constraint(
        "uq_offer_installment_options_terms",
        "offer_installment_options",
        type_="unique",
    )
    op.drop_column("offer_installment_options", "payment_method")
