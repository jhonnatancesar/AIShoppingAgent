"""Add commercial condition to price observations (TASK-094).

Revision ID: 20260822_0002
Revises: 20260822_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0002"
down_revision: str | None = "20260822_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "price_observations",
        sa.Column(
            "condition",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_check_constraint(
        "ck_price_observations_condition_values",
        "price_observations",
        "condition IN ('new', 'refurbished', 'used', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_price_observations_condition_values",
        "price_observations",
        type_="check",
    )
    op.drop_column("price_observations", "condition")
