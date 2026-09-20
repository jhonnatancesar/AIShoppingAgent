"""Histórico real de pesquisas (Frente 5, correção de escopo 2026-09-12):
`search_receipts` (TASK-107) já gravava UMA linha por pesquisa aceita,
mas só para contagem de cota (`user_id`/`created_at`), sem o texto
pesquisado nem ligação com o que foi encontrado. Agregar
`MissionCriteria.search_query` de missões `ACTIVE` (tentativa anterior)
não representava pesquisas de fato -- perdia toda pesquisa feita em
`/app/search` que nunca virou missão, e expunha texto livre do usuário
sem filtro de conteúdo sensível.

`query_text` grava o texto real pesquisado (nulo para linhas antigas,
gravadas antes desta migração -- nunca inventado por backfill).
`search_receipt_products` liga cada pesquisa aos `Product`s
RECONHECIDOS (`identity_key IS NOT NULL`) que ela retornou -- é essa
ligação, não o texto livre, que alimenta a visão comunitária ("Veja o
que estão pesquisando"): só nomes de produto canônicos e vetados
aparecem lá, nunca o que o usuário digitou.

Revision ID: 20260912_0004
Revises: 20260912_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0004"
down_revision: str | None = "20260912_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_receipts",
        sa.Column("query_text", sa.Text(), nullable=True),
    )
    op.create_table(
        "search_receipt_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "search_receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("search_receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_search_receipt_products_receipt_product",
        "search_receipt_products",
        ["search_receipt_id", "product_id"],
        unique=True,
    )
    op.create_index(
        "ix_search_receipt_products_product_id",
        "search_receipt_products",
        ["product_id"],
    )


def downgrade() -> None:
    op.drop_table("search_receipt_products")
    op.drop_column("search_receipts", "query_text")
