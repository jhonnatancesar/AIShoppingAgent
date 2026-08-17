"""Add offer_installment_options table (TASK-089, DEC-069).

Revision ID: 20260817_0001
Revises: 20260816_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260817_0001"
down_revision: str | None = "20260816_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INTEREST_KIND_VALUES = "('interest_free', 'with_interest', 'unknown')"


def upgrade() -> None:
    op.create_table(
        "offer_installment_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "price_observation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("installment_count", sa.Integer(), nullable=False),
        sa.Column("installment_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("installment_total_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "interest_kind",
            sa.String(length=32),
            server_default="unknown",
            nullable=False,
        ),
        sa.Column(
            "is_highlighted",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.CheckConstraint(
            "installment_count > 0",
            name="ck_offer_installment_options_count_positive",
        ),
        sa.CheckConstraint(
            "installment_amount > 0",
            name="ck_offer_installment_options_amount_positive",
        ),
        sa.CheckConstraint(
            "installment_total_amount IS NULL OR installment_total_amount > 0",
            name="ck_offer_installment_options_total_positive",
        ),
        sa.CheckConstraint(
            "discount_percent IS NULL OR "
            "(discount_percent >= 0 AND discount_percent <= 100)",
            name="ck_offer_installment_options_discount_range",
        ),
        sa.CheckConstraint(
            f"interest_kind IN {_INTEREST_KIND_VALUES}",
            name="ck_offer_installment_options_interest_kind_values",
        ),
        sa.ForeignKeyConstraint(
            ["price_observation_id"],
            ["price_observations.id"],
            name=op.f(
                "fk_offer_installment_options_price_observation_id_price_observations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offer_installment_options")),
        sa.UniqueConstraint(
            "price_observation_id",
            "installment_count",
            name="uq_offer_installment_options_observation_count",
        ),
    )
    op.create_index(
        "ix_offer_installment_options_price_observation_id",
        "offer_installment_options",
        ["price_observation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_offer_installment_options_price_observation_id",
        table_name="offer_installment_options",
    )
    op.drop_table("offer_installment_options")
