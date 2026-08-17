"""Add seller and fulfillment classification to price observations.

Revision ID: 20260816_0002
Revises: 20260816_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0002"
down_revision: str | None = "20260816_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VALUES = "('platform', 'marketplace_partner', 'unknown')"


def upgrade() -> None:
    op.add_column(
        "price_observations", sa.Column("seller_kind", sa.String(32), nullable=True)
    )
    op.add_column(
        "price_observations",
        sa.Column("fulfillment_kind", sa.String(32), nullable=True),
    )
    op.create_check_constraint(
        "ck_price_observations_seller_kind_values",
        "price_observations",
        f"seller_kind IS NULL OR seller_kind IN {_VALUES}",
    )
    op.create_check_constraint(
        "ck_price_observations_fulfillment_kind_values",
        "price_observations",
        f"fulfillment_kind IS NULL OR fulfillment_kind IN {_VALUES}",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_price_observations_fulfillment_kind_values",
        "price_observations",
        type_="check",
    )
    op.drop_constraint(
        "ck_price_observations_seller_kind_values",
        "price_observations",
        type_="check",
    )
    op.drop_column("price_observations", "fulfillment_kind")
    op.drop_column("price_observations", "seller_kind")
