"""Cria `product_identity_candidates` -- aprendizado de identidade de
produto assistido por IA (2026-09-12): quando nenhum extrator
determinístico de `app.products.identity` reconhece um título, uma
proposta estruturada (marca/família/modelo/variante/atributos),
validada por grounding determinístico, é persistida aqui -- nunca uma
regra/regex genérica gerada por IA (a fórmula de `family_key`/
`identity_key` continua sendo código puro).

Conceitualmente separado de `product_identity_aliases`
(`app.products.models.ProductIdentityAlias`, TASK-112 -- correção de
ESCRITA de um valor de atributo já conhecido dentro de uma categoria
existente): esta tabela aprende uma FAMÍLIA/MODELO inteira nova a
partir de texto livre, com sua própria fila de revisão. Nenhuma
sobrecarrega a outra.

`normalized_title_hash` UNIQUE é a chave de reuso -- o MESMO título
(de qualquer loja) nunca dispara uma segunda chamada de IA depois que
já existe uma linha para esse hash.

Revision ID: 20260912_0003
Revises: 20260912_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260912_0003"
down_revision: str | None = "20260912_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_identity_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_title", sa.Text(), nullable=False),
        sa.Column("normalized_title_hash", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("brand", sa.String(length=160), nullable=False),
        sa.Column("family", sa.String(length=160), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("variant", sa.String(length=160), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("family_key", sa.String(length=80), nullable=False),
        sa.Column("identity_key", sa.String(length=80), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending_review",
        ),
        sa.Column("grounded", sa.Boolean(), nullable=False),
        sa.Column("ai_provider", sa.String(length=80), nullable=True),
        sa.Column("ai_model", sa.String(length=120), nullable=True),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('approved', 'pending_review', 'rejected')",
            name="ck_product_identity_candidates_status_values",
        ),
        sa.CheckConstraint(
            "btrim(raw_title) <> ''",
            name="ck_product_identity_candidates_raw_title_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(category) <> ''",
            name="ck_product_identity_candidates_category_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(brand) <> ''", name="ck_product_identity_candidates_brand_not_blank"
        ),
        sa.CheckConstraint(
            "btrim(family) <> ''",
            name="ck_product_identity_candidates_family_not_blank",
        ),
        sa.CheckConstraint(
            "btrim(model) <> ''", name="ck_product_identity_candidates_model_not_blank"
        ),
    )
    op.create_index(
        "uq_product_identity_candidates_title_hash",
        "product_identity_candidates",
        ["normalized_title_hash"],
        unique=True,
    )
    op.create_index(
        "ix_product_identity_candidates_status",
        "product_identity_candidates",
        ["status"],
    )
    op.create_index(
        "ix_product_identity_candidates_identity_key",
        "product_identity_candidates",
        ["identity_key"],
    )


def downgrade() -> None:
    op.drop_table("product_identity_candidates")
