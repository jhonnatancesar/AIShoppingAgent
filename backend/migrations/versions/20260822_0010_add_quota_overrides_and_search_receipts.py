"""Add per-user quota overrides and search receipts (TASK-107).

Revision ID: 20260822_0010
Revises: 20260822_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260822_0010"
down_revision: str | None = "20260822_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("max_active_missions_override", sa.Integer(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("max_store_slots_override", sa.Integer(), nullable=True)
    )
    op.add_column(
        "users", sa.Column("max_daily_searches_override", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "ck_users_max_active_missions_override_positive",
        "users",
        "max_active_missions_override IS NULL OR max_active_missions_override > 0",
    )
    op.create_check_constraint(
        "ck_users_max_store_slots_override_positive",
        "users",
        "max_store_slots_override IS NULL OR max_store_slots_override > 0",
    )
    op.create_check_constraint(
        "ck_users_max_daily_searches_override_positive",
        "users",
        "max_daily_searches_override IS NULL OR max_daily_searches_override > 0",
    )

    op.create_table(
        "search_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_search_receipts_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_receipts")),
    )
    op.create_index(
        "ix_search_receipts_user_created_at",
        "search_receipts",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_receipts_user_created_at", table_name="search_receipts")
    op.drop_table("search_receipts")
    op.drop_constraint(
        "ck_users_max_daily_searches_override_positive", "users", type_="check"
    )
    op.drop_constraint(
        "ck_users_max_store_slots_override_positive", "users", type_="check"
    )
    op.drop_constraint(
        "ck_users_max_active_missions_override_positive", "users", type_="check"
    )
    op.drop_column("users", "max_daily_searches_override")
    op.drop_column("users", "max_store_slots_override")
    op.drop_column("users", "max_active_missions_override")
