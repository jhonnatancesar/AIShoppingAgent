"""TASK-136: destaque da missão (ofertas que o funil escolheu no último ciclo).

Revision ID: 20261003_0001
Revises: 20261002_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261003_0001"
down_revision: str | None = "20261002_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mission_offer_highlights",
        sa.Column(
            "mission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("missions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "offer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("offers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "chosen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("mission_offer_highlights")
