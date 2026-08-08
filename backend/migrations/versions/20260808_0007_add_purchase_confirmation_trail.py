"""Adiciona confirmação persistente e trilha append-only (TASK-041).

Revision ID: 20260808_0007
Revises: 20260808_0006
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808_0007"
down_revision: str | None = "20260808_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

entry_type = postgresql.ENUM(
    "requested",
    "confirmed",
    "cancelled",
    "stale",
    name="purchase_trail_entry_type",
    create_type=False,
)
decision = postgresql.ENUM(
    "confirm",
    "cancel",
    name="purchase_confirmation_decision",
    create_type=False,
)
stale_reason = postgresql.ENUM(
    "expired",
    "evidence_changed",
    name="purchase_confirmation_stale_reason",
    create_type=False,
)
availability = postgresql.ENUM(name="offer_availability", create_type=False)


def upgrade() -> None:
    """Persiste a solicitação e sua resolução sem permitir mutações."""
    bind = op.get_bind()
    entry_type.create(bind, checkfirst=True)
    decision.create(bind, checkfirst=True)
    stale_reason.create(bind, checkfirst=True)

    op.create_table(
        "purchase_confirmations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "price_observation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("shipping_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("total_amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("availability", availability, nullable=False),
        sa.Column("fulfillment", sa.String(120), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("position >= 1", name="ck_purchase_confirmations_position"),
        sa.CheckConstraint("amount >= 0", name="ck_purchase_confirmations_amount"),
        sa.CheckConstraint(
            "shipping_amount >= 0",
            name="ck_purchase_confirmations_shipping_amount",
        ),
        sa.CheckConstraint(
            "total_amount = amount + shipping_amount",
            name="ck_purchase_confirmations_total_exact",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_purchase_confirmations_currency_iso4217",
        ),
        sa.CheckConstraint(
            "expires_at = requested_at + INTERVAL '15 minutes'",
            name="ck_purchase_confirmations_ttl",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_snapshot) = 'object'",
            name="ck_purchase_confirmations_snapshot_object",
        ),
        sa.CheckConstraint("btrim(url) <> ''", name="ck_purchase_confirmations_url"),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["price_observation_id"],
            ["price_observations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["seller_id"], ["sellers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "mission_id",
            "owner_user_id",
            "offer_id",
            "price_observation_id",
            name="uq_purchase_confirmations_identity",
        ),
    )
    op.create_index(
        "ix_purchase_confirmations_mission",
        "purchase_confirmations",
        ["mission_id", "requested_at", "id"],
    )
    op.create_index(
        "ix_purchase_confirmations_owner",
        "purchase_confirmations",
        ["owner_user_id", "requested_at"],
    )

    op.create_table(
        "purchase_trail_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "price_observation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("entry_type", entry_type, nullable=False),
        sa.Column("decision", decision, nullable=True),
        sa.Column("stale_reason", stale_reason, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(entry_type = 'requested' AND decision IS NULL "
            "AND stale_reason IS NULL AND resolved_at IS NULL) OR "
            "(entry_type = 'confirmed' AND decision = 'confirm' "
            "AND stale_reason IS NULL AND resolved_at IS NOT NULL) OR "
            "(entry_type = 'cancelled' AND decision = 'cancel' "
            "AND stale_reason IS NULL AND resolved_at IS NOT NULL) OR "
            "(entry_type = 'stale' AND decision = 'confirm' "
            "AND stale_reason IN ('expired', 'evidence_changed') "
            "AND resolved_at IS NOT NULL)",
            name="ck_purchase_trail_entries_resolution_matrix",
        ),
        sa.ForeignKeyConstraint(
            ["confirmation_id"],
            ["purchase_confirmations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["price_observation_id"],
            ["price_observations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "confirmation_id",
                "mission_id",
                "owner_user_id",
                "offer_id",
                "price_observation_id",
            ],
            [
                "purchase_confirmations.id",
                "purchase_confirmations.mission_id",
                "purchase_confirmations.owner_user_id",
                "purchase_confirmations.offer_id",
                "purchase_confirmations.price_observation_id",
            ],
            name="fk_purchase_trail_entries_confirmation_identity",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_purchase_trail_entries_requested_confirmation",
        "purchase_trail_entries",
        ["confirmation_id"],
        unique=True,
        postgresql_where=sa.text("entry_type = 'requested'"),
    )
    op.create_index(
        "ux_purchase_trail_entries_terminal_confirmation",
        "purchase_trail_entries",
        ["confirmation_id"],
        unique=True,
        postgresql_where=sa.text("entry_type IN ('confirmed', 'cancelled', 'stale')"),
    )
    op.create_index(
        "ix_purchase_trail_entries_mission_history",
        "purchase_trail_entries",
        ["mission_id", "recorded_at", "id"],
    )

    op.execute(
        """
        CREATE FUNCTION block_purchase_trail_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in ("purchase_confirmations", "purchase_trail_entries"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION block_purchase_trail_mutation()
            """
        )


def downgrade() -> None:
    """Remove somente as estruturas introduzidas pela TASK-041."""
    for table_name in ("purchase_trail_entries", "purchase_confirmations"):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION block_purchase_trail_mutation()")
    op.drop_table("purchase_trail_entries")
    op.drop_table("purchase_confirmations")

    bind = op.get_bind()
    stale_reason.drop(bind, checkfirst=True)
    decision.drop(bind, checkfirst=True)
    entry_type.drop(bind, checkfirst=True)
