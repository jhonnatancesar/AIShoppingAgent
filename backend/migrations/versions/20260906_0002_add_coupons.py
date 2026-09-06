"""Adiciona `coupons` e `coupon_offer_links` -- consumo de cupons pelo GG
Oferta (decisão de arquitetura de 2026-09-06): o Coupon Worker roda na
mesma máquina e usa o mesmo PostgreSQL do GG Oferta, sem sync de SQLite
nem API intermediária. `store_id` é FK real para `stores.id` (nunca
texto solto). O vínculo com `Offer` fica numa tabela de associação
separada, nunca uma coluna em `coupons` -- um mesmo cupom pode se
aplicar a mais de uma oferta; um cupom sem nenhuma linha em
`coupon_offer_links` é simplesmente genérico, ainda não avaliado.
`scope_kind`/`scope_reference`/`valid_until` são preservados CRUS (texto),
sem nenhum parsing/validação -- a regra de aplicabilidade ainda não foi
definida. `coupon_offer_links.offer_id` usa `ondelete="CASCADE"`
(correção pós-revisão): apagar uma `Offer` remove só o vínculo, nunca o
`Coupon` -- uma `Offer` nunca fica impossível de excluir só por ter
cupom associado.

Revision ID: 20260906_0002
Revises: 20260906_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260906_0002"
down_revision: str | None = "20260906_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "code", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("discount_kind", sa.String(20), nullable=True),
        sa.Column("discount_value", sa.Numeric(19, 4), nullable=True),
        sa.Column("minimum_purchase_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("maximum_discount_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("scope_kind", sa.String(20), nullable=True),
        sa.Column("scope_reference", sa.Text(), nullable=True),
        sa.Column("valid_until", sa.Text(), nullable=True),
        sa.Column("raw_rule_text", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(evidence) <> ''", name="ck_coupons_evidence_not_blank"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "store_id", "code", "evidence", name="uq_coupons_evidence"
        ),
    )
    op.create_index("ix_coupons_store_status", "coupons", ["store_id", "status"])

    op.create_table(
        "coupon_offer_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("coupon_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"], ondelete="RESTRICT"),
        # CASCADE (correção): apagar uma Offer remove só o vínculo, nunca
        # o Coupon (que não tem FK direta pra Offer) -- uma Offer nunca
        # pode ficar impossível de excluir só por ter cupom associado.
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "coupon_id", "offer_id", name="uq_coupon_offer_links_pair"
        ),
    )
    op.create_index(
        "ix_coupon_offer_links_offer", "coupon_offer_links", ["offer_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_coupon_offer_links_offer", table_name="coupon_offer_links")
    op.drop_table("coupon_offer_links")
    op.drop_index("ix_coupons_store_status", table_name="coupons")
    op.drop_table("coupons")
