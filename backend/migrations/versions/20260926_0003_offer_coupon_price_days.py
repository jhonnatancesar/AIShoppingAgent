"""TASK-125: o preço com cupom calculado pela coleta passa a ser guardado
por (Offer, observação de preço, dia comercial), para o gráfico de
histórico mostrar o ponto do dia no preço com cupom e, no tooltip, o
preço normal e o cupom usado. Decisão do usuário (2026-09-26): só daqui
pra frente -- nenhum dado antigo é reconstruído (tabela nasce vazia).

Revision ID: 20260926_0003
Revises: 20260926_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260926_0003"
down_revision: str | None = "20260926_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "offer_coupon_price_days"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("commercial_day", sa.Date(), nullable=False),
        sa.Column("coupon_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("coupon_code", sa.Text(), nullable=False),
        sa.Column("original_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("discount_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("final_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "discount_amount > 0",
            name="ck_offer_coupon_price_days_discount_positive",
        ),
        sa.CheckConstraint(
            "final_amount >= 0",
            name="ck_offer_coupon_price_days_final_non_negative",
        ),
        sa.CheckConstraint(
            "final_amount = original_amount - discount_amount",
            name="ck_offer_coupon_price_days_final_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["offers.id"],
            name=op.f("fk_offer_coupon_price_days_offer_id_offers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["price_observations.id"],
            name=op.f("fk_offer_coupon_price_days_observation_id_price_observations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["coupon_id"],
            ["coupons.id"],
            name=op.f("fk_offer_coupon_price_days_coupon_id_coupons"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offer_coupon_price_days")),
        sa.UniqueConstraint(
            "offer_id",
            "observation_id",
            "commercial_day",
            name="uq_offer_coupon_price_days_confirmation",
        ),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
