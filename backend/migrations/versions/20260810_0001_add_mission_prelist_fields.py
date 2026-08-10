"""Adiciona rastreamento da pré-lista informativa de preços (TASK-068).

Revision ID: 20260810_0001
Revises: 20260809_0004
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260810_0001"
down_revision: str | None = "20260809_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "missions",
        sa.Column(
            "prelist_sent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "missions",
        sa.Column(
            "prelist_errata_sent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "missions",
        sa.Column("prelist_lowest_amount", sa.Numeric(19, 4), nullable=True),
    )
    op.add_column(
        "missions",
        sa.Column("prelist_lowest_currency", sa.String(3), nullable=True),
    )
    op.create_check_constraint(
        "ck_missions_prelist_lowest_pair",
        "missions",
        "(prelist_lowest_amount IS NULL AND prelist_lowest_currency IS NULL) OR "
        "(prelist_lowest_amount IS NOT NULL AND prelist_lowest_currency IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_missions_prelist_lowest_amount_non_negative",
        "missions",
        "prelist_lowest_amount IS NULL OR prelist_lowest_amount >= 0",
    )
    op.create_check_constraint(
        "ck_missions_prelist_lowest_currency_iso4217",
        "missions",
        "prelist_lowest_currency IS NULL OR prelist_lowest_currency ~ '^[A-Z]{3}$'",
    )
    op.create_check_constraint(
        "ck_missions_prelist_errata_requires_sent",
        "missions",
        "prelist_sent = true OR prelist_errata_sent = false",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_missions_prelist_errata_requires_sent", "missions", type_="check"
    )
    op.drop_constraint(
        "ck_missions_prelist_lowest_currency_iso4217", "missions", type_="check"
    )
    op.drop_constraint(
        "ck_missions_prelist_lowest_amount_non_negative", "missions", type_="check"
    )
    op.drop_constraint("ck_missions_prelist_lowest_pair", "missions", type_="check")
    op.drop_column("missions", "prelist_lowest_currency")
    op.drop_column("missions", "prelist_lowest_amount")
    op.drop_column("missions", "prelist_errata_sent")
    op.drop_column("missions", "prelist_sent")
