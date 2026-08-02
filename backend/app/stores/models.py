"""Modelo persistente das lojas que publicam ofertas."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class Store(Base):
    """Origem nacional normalizada, sem implementar integração de coleta."""

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
