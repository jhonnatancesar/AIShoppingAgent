"""Adiciona imagem canônica ao Product (subtask 4, auditoria GG Oferta).

Revision ID: 20260830_0001
Revises: 20260828_0001

Hoje cada Offer carrega sua própria image_url, sem noção de produto
compartilhado -- o mesmo produto exato vendido em 4 lojas diferentes
podia mostrar 4 fotos diferentes sem necessidade. `canonical_image_url`
é opcional e nunca substitui `Offer.image_url` (que continua existindo
como proveniência/override quando fizer sentido usar a foto específica
da oferta) -- é só um fallback de apresentação quando a oferta não tem
imagem própria. Nenhum dado existente é alterado; a coluna nasce NULL
para todo Product já persistido e é populada de forma incremental pela
próxima coleta de cada um (ver
`app.collection.orchestration._maybe_set_canonical_image`).
"""

import sqlalchemy as sa
from alembic import op

revision = "20260830_0001"
down_revision = "20260828_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("canonical_image_url", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_products_canonical_image_url_http",
        "products",
        "canonical_image_url IS NULL OR "
        "canonical_image_url ~* '^https?://[^/@?#[:space:]]+([/?#]|$)'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_products_canonical_image_url_http", "products", type_="check"
    )
    op.drop_column("products", "canonical_image_url")
