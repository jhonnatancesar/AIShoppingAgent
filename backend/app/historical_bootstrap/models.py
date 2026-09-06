"""Evidências históricas externas imutáveis e estado one-shot da FASE F1."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class HistoricalBootstrapStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED_WITH_REFERENCES = "completed_with_references"
    COMPLETED_WITHOUT_REFERENCES = "completed_without_references"


class HistoricalBootstrap(Base):
    __tablename__ = "historical_bootstraps"
    __table_args__ = (
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_historical_bootstraps_currency"
        ),
        UniqueConstraint(
            "product_id", "condition", "currency", name="uq_historical_bootstraps_scope"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    condition: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[HistoricalBootstrapStatus] = mapped_column(
        Enum(
            HistoricalBootstrapStatus,
            name="historical_bootstrap_status",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExternalPriceReference(Base):
    __tablename__ = "external_price_references"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_external_price_references_amount"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_external_price_references_currency"
        ),
        CheckConstraint(
            "btrim(source) <> ''", name="ck_external_price_references_source"
        ),
        CheckConstraint(
            "btrim(safe_url) <> ''", name="ck_external_price_references_url"
        ),
        UniqueConstraint(
            "bootstrap_id",
            "source",
            "safe_url",
            "amount",
            "historical_date",
            name="uq_external_price_references_evidence",
        ),
        Index("ix_external_price_references_product", "product_id", "collected_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    bootstrap_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("historical_bootstraps.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    reference_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="historical_price"
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    historical_date: Mapped[date | None] = mapped_column(Date)
    store_name: Mapped[str | None] = mapped_column(String(160))
    safe_url: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[str] = mapped_column(String(16), nullable=False)
    matched_identity_key: Mapped[str] = mapped_column(String(80), nullable=False)
    match_evidence: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    quality: Mapped[str] = mapped_column(String(16), nullable=False, default="verified")
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
