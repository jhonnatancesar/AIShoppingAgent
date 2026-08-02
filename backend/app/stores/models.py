"""Modelo persistente das lojas que publicam ofertas."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class StoreSourceType(StrEnum):
    """Natureza comercial da fonte de ofertas."""

    RETAILER = "retailer"
    MARKETPLACE = "marketplace"


class Store(Base):
    """Fonte normalizada de ofertas, sem implementar integração de coleta."""

    __tablename__ = "stores"
    __table_args__ = (
        CheckConstraint(
            "code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'",
            name="ck_stores_code_snake_case",
        ),
        CheckConstraint("btrim(name) <> ''", name="ck_stores_name_not_blank"),
        CheckConstraint(
            "btrim(base_url) <> ''",
            name="ck_stores_base_url_not_blank",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[StoreSourceType] = mapped_column(
        Enum(
            StoreSourceType,
            name="store_source_type_values",
            values_callable=lambda values: [value.value for value in values],
            validate_strings=True,
            native_enum=False,
            create_constraint=True,
            length=16,
        ),
        nullable=False,
        default=StoreSourceType.RETAILER,
        server_default=StoreSourceType.RETAILER.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
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


class Seller(Base):
    """Vendedor estável que publica ofertas dentro de um marketplace."""

    __tablename__ = "sellers"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="ck_sellers_name_not_blank"),
        CheckConstraint(
            "external_id IS NULL OR btrim(external_id) <> ''",
            name="ck_sellers_external_id_not_blank",
        ),
        UniqueConstraint("id", "store_id", name="uq_sellers_id_store_id"),
        Index("ix_sellers_store_id", "store_id"),
        Index(
            "uq_sellers_store_external_id",
            "store_id",
            "external_id",
            unique=True,
            postgresql_where="external_id IS NOT NULL",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
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
