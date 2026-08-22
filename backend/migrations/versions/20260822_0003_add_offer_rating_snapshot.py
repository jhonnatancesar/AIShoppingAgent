"""Add source-bound current rating snapshot to offers (TASK-096).

Revision ID: 20260822_0003
Revises: 20260822_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0003"
down_revision: str | None = "20260822_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "offers", sa.Column("rating_average", sa.Numeric(3, 2), nullable=True)
    )
    op.add_column(
        "offers", sa.Column("review_count", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "offers",
        sa.Column("rating_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_offers_rating_average_range",
        "offers",
        "rating_average IS NULL OR "
        "(rating_average >= 0 AND rating_average <= 5)",
    )
    op.create_check_constraint(
        "ck_offers_review_count_non_negative",
        "offers",
        "review_count IS NULL OR review_count >= 0",
    )
    op.create_check_constraint(
        "ck_offers_rating_snapshot_complete",
        "offers",
        "(rating_average IS NULL AND review_count IS NULL AND "
        "rating_observed_at IS NULL) OR "
        "(rating_average IS NOT NULL AND review_count IS NOT NULL AND "
        "rating_observed_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_offers_rating_snapshot_complete", "offers", type_="check"
    )
    op.drop_constraint(
        "ck_offers_review_count_non_negative", "offers", type_="check"
    )
    op.drop_constraint(
        "ck_offers_rating_average_range", "offers", type_="check"
    )
    op.drop_column("offers", "rating_observed_at")
    op.drop_column("offers", "review_count")
    op.drop_column("offers", "rating_average")
