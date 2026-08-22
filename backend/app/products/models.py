"""Modelo persistente de produtos canônicos."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class Product(Base):
    """Identidade canônica de um item, independente de loja e oferta."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "btrim(name) <> ''",
            name="ck_products_name_not_blank",
        ),
        CheckConstraint(
            "brand IS NULL OR btrim(brand) <> ''",
            name="ck_products_brand_not_blank",
        ),
        CheckConstraint(
            "model IS NULL OR btrim(model) <> ''",
            name="ck_products_model_not_blank",
        ),
        CheckConstraint(
            "display_name IS NULL OR btrim(display_name) <> ''",
            name="ck_products_display_name_not_blank",
        ),
        CheckConstraint(
            "identity_key IS NULL OR (identity_version IS NOT NULL AND "
            "category IS NOT NULL AND brand IS NOT NULL AND family IS NOT NULL "
            "AND model IS NOT NULL AND variant IS NOT NULL AND family_key IS NOT NULL)",
            name="ck_products_identity_complete",
        ),
        CheckConstraint(
            "identity_version IS NULL OR identity_version > 0",
            name="ck_products_identity_version_positive",
        ),
        Index(
            "uq_products_identity_key",
            "identity_key",
            unique=True,
            postgresql_where="identity_key IS NOT NULL",
        ),
        Index("ix_products_family_key", "family_key"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    family: Mapped[str | None] = mapped_column(String(160), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attributes: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    family_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    identity_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    identity_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
