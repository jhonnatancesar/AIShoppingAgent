"""Adiciona título de exibição em products e classificação de relevância
(mission_id, offer_id) para a TASK-063.

Revision ID: 20260809_0004
Revises: 20260809_0003
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260809_0004"
down_revision: str | None = "20260809_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("display_name", sa.String(300), nullable=True),
    )
    op.create_check_constraint(
        "ck_products_display_name_not_blank",
        "products",
        "display_name IS NULL OR btrim(display_name) <> ''",
    )

    relevance = postgresql.ENUM(
        "match",
        "possible_match",
        "no_match",
        name="offer_relevance",
        create_type=False,
    )
    relevance.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "mission_offer_relevance",
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classification", relevance, nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("mission_id", "offer_id"),
    )
    op.create_index(
        "ix_mission_offer_relevance_offer_id",
        "mission_offer_relevance",
        ["offer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mission_offer_relevance_offer_id",
        table_name="mission_offer_relevance",
    )
    op.drop_table("mission_offer_relevance")
    postgresql.ENUM(name="offer_relevance").drop(op.get_bind(), checkfirst=True)

    op.drop_constraint("ck_products_display_name_not_blank", "products", type_="check")
    op.drop_column("products", "display_name")
