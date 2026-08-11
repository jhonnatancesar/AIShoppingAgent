"""Adiciona modelo estruturado do produto ao critério da missão (TASK-075).

Revision ID: 20260811_0001
Revises: 20260810_0001
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0001"
down_revision: str | None = "20260810_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mission_criteria",
        sa.Column("model", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_mission_criteria_model_not_blank",
        "mission_criteria",
        "model IS NULL OR btrim(model) <> ''",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mission_criteria_model_not_blank", "mission_criteria", type_="check"
    )
    op.drop_column("mission_criteria", "model")
