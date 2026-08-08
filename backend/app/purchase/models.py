"""Solicitações imutáveis e trilha append-only de confirmação de compra."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.collection.normalization import Availability
from app.database.base import Base
from app.purchase.confirmation import (
    PurchaseConfirmationDecision,
    PurchaseConfirmationStaleReason,
)


class PurchaseTrailEntryType(StrEnum):
    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    STALE = "stale"


class PurchaseConfirmation(Base):
    """Evidência imutável apresentada ao proprietário de uma missão."""

    __tablename__ = "purchase_confirmations"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_purchase_confirmations_position"),
        CheckConstraint("amount >= 0", name="ck_purchase_confirmations_amount"),
        CheckConstraint(
            "shipping_amount >= 0",
            name="ck_purchase_confirmations_shipping_amount",
        ),
        CheckConstraint(
            "total_amount = amount + shipping_amount",
            name="ck_purchase_confirmations_total_exact",
        ),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_purchase_confirmations_currency_iso4217",
        ),
        CheckConstraint(
            "expires_at = requested_at + INTERVAL '15 minutes'",
            name="ck_purchase_confirmations_ttl",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_snapshot) = 'object'",
            name="ck_purchase_confirmations_snapshot_object",
        ),
        CheckConstraint("btrim(url) <> ''", name="ck_purchase_confirmations_url"),
        UniqueConstraint(
            "id",
            "mission_id",
            "owner_user_id",
            "offer_id",
            "price_observation_id",
            name="uq_purchase_confirmations_identity",
        ),
        Index("ix_purchase_confirmations_mission", "mission_id", "requested_at", "id"),
        Index("ix_purchase_confirmations_owner", "owner_user_id", "requested_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    price_observation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("price_observations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seller_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="RESTRICT"),
        nullable=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    availability: Mapped[Availability] = mapped_column(
        Enum(
            Availability,
            name="offer_availability",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    fulfillment: Mapped[str | None] = mapped_column(String(120), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PurchaseTrailEntry(Base):
    """Fato imutável da criação ou resolução de uma confirmação."""

    __tablename__ = "purchase_trail_entries"
    __table_args__ = (
        CheckConstraint(
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
        ForeignKeyConstraint(
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
        Index(
            "ux_purchase_trail_entries_requested_confirmation",
            "confirmation_id",
            unique=True,
            postgresql_where=text("entry_type = 'requested'"),
        ),
        Index(
            "ux_purchase_trail_entries_terminal_confirmation",
            "confirmation_id",
            unique=True,
            postgresql_where=text("entry_type IN ('confirmed', 'cancelled', 'stale')"),
        ),
        Index(
            "ix_purchase_trail_entries_mission_history",
            "mission_id",
            "recorded_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    confirmation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("purchase_confirmations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    price_observation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("price_observations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_type: Mapped[PurchaseTrailEntryType] = mapped_column(
        Enum(
            PurchaseTrailEntryType,
            name="purchase_trail_entry_type",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    decision: Mapped[PurchaseConfirmationDecision | None] = mapped_column(
        Enum(
            PurchaseConfirmationDecision,
            name="purchase_confirmation_decision",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=True,
    )
    stale_reason: Mapped[PurchaseConfirmationStaleReason | None] = mapped_column(
        Enum(
            PurchaseConfirmationStaleReason,
            name="purchase_confirmation_stale_reason",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
