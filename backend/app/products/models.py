"""Modelo persistente de produtos canônicos."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, String, func
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
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(160), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
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
