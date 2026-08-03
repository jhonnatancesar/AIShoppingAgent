"""Cria observações imutáveis de preço."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260802_0012"
down_revision = "20260802_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    availability = postgresql.ENUM(
        "available",
        "unavailable",
        "unknown",
        name="offer_availability",
        create_type=False,
    )
    availability.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "price_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("shipping_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("total_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("fulfillment", sa.String(120), nullable=True),
        sa.Column("availability", availability, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("raw_evidence", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "amount >= 0 AND (shipping_amount IS NULL OR shipping_amount >= 0)",
            name="ck_price_observations_amounts_non_negative",
        ),
        sa.CheckConstraint(
            "total_amount = amount + COALESCE(shipping_amount, 0)",
            name="ck_price_observations_total_exact",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_price_observations_currency_iso4217"
        ),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["collection_run_id"], ["collection_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_price_observations_offer_observed",
        "price_observations",
        ["offer_id", sa.text("observed_at DESC"), "id"],
    )
    op.create_index(
        "ix_price_observations_collection_run_id",
        "price_observations",
        ["collection_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_price_observations_collection_run_id", table_name="price_observations"
    )
    op.drop_index(
        "ix_price_observations_offer_observed", table_name="price_observations"
    )
    op.drop_table("price_observations")
    postgresql.ENUM(name="offer_availability").drop(op.get_bind(), checkfirst=True)
