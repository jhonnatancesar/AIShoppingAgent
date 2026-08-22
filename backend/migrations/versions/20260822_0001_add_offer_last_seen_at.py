"""Add offers.last_seen_at (TASK-093, redução de PriceObservation redundante).

Revision ID: 20260822_0001
Revises: 20260821_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0001"
down_revision: str | None = "20260821_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE offers
        SET last_seen_at = COALESCE(
            (SELECT MAX(observed_at) FROM price_observations
             WHERE price_observations.offer_id = offers.id),
            offers.created_at
        )
        """
    )
    op.alter_column(
        "offers",
        "last_seen_at",
        nullable=False,
        server_default=sa.text("now()"),
    )


def downgrade() -> None:
    op.drop_column("offers", "last_seen_at")
