"""Persistência das execuções rastreáveis de coleta."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.collection.normalization import Availability
from app.database.base import Base
from app.database.time import utc_now


class CollectionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR (status IN ('succeeded', 'failed') AND finished_at IS NOT NULL)",
            name="ck_collection_runs_terminal_finished",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_collection_runs_time_order",
        ),
        Index(
            "ix_collection_runs_mission_started_at", "mission_id", desc("started_at")
        ),
        Index("ix_collection_runs_store_started_at", "store_id", desc("started_at")),
    )
    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mission_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[CollectionRunStatus] = mapped_column(
        Enum(
            CollectionRunStatus,
            name="collection_run_status",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
        default=CollectionRunStatus.RUNNING,
        server_default=CollectionRunStatus.RUNNING.value,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class PriceObservation(Base):
    __tablename__ = "price_observations"
    __table_args__ = (
        CheckConstraint(
            "amount >= 0 AND (shipping_amount IS NULL OR shipping_amount >= 0)",
            name="ck_price_observations_amounts_non_negative",
        ),
        CheckConstraint(
            "total_amount = amount + COALESCE(shipping_amount, 0)",
            name="ck_price_observations_total_exact",
        ),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_price_observations_currency_iso4217"
        ),
        Index(
            "ix_price_observations_offer_observed",
            "offer_id",
            desc("observed_at"),
            "id",
        ),
        Index("ix_price_observations_collection_run_id", "collection_run_id"),
    )
    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    collection_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    shipping_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(19, 4), nullable=True
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    fulfillment: Mapped[str | None] = mapped_column(String(120), nullable=True)
    availability: Mapped[Availability] = mapped_column(
        Enum(
            Availability,
            name="offer_availability",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    raw_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
