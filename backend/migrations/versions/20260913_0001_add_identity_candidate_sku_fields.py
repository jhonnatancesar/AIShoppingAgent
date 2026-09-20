"""Adiciona `store_sku` e `manufacturer_part_number` a
`product_identity_candidates` (checkpoint 3, 2026-09-13): a camada
global de identidade precisa distinguir o SKU que a PRÓPRIA loja
atribui ao anúncio (nunca um identificador cross-store confiável) do
part number/código de peça atribuído pelo FABRICANTE (evidência forte
de mesma variante quando duas lojas exibem o mesmo valor). Antes desta
migration, esse dado -- quando a IA o extraía -- era misturado dentro
do JSONB genérico `attributes`, o que o fazia entrar na fórmula do
`identity_key` (`app.products.identity._identity_key`) e bloquear
reaproveitamento entre lojas só por ausência de um SKU específico de
uma delas (achado real, par Terabyte/Pichau da mesma placa-mãe TUF
Gaming B650M-E WiFi). Os dois campos ficam nullable e fora da fórmula
de identidade -- servem apenas como evidência para o resolvedor
determinístico e para o árbitro de IA.

Revision ID: 20260913_0001
Revises: 20260912_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_0001"
down_revision: str | None = "20260912_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_identity_candidates",
        sa.Column("store_sku", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "product_identity_candidates",
        sa.Column("manufacturer_part_number", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_identity_candidates", "manufacturer_part_number")
    op.drop_column("product_identity_candidates", "store_sku")
