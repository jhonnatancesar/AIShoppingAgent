"""Recibos mínimos e append-only de updates autenticados do Telegram."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TelegramUpdateDisposition(StrEnum):
    ACCEPTED = "accepted"
    RATE_LIMITED = "rate_limited"
    DISCARDED = "discarded"


class TelegramUpdateReceipt(Base):
    """Fato terminal que participa da mesma transação dos efeitos do update."""

    __tablename__ = "telegram_update_receipts"
    __table_args__ = (
        CheckConstraint(
            "disposition IN ('accepted', 'rate_limited', 'discarded')",
            name="ck_telegram_update_receipts_disposition",
        ),
        UniqueConstraint("update_id", name="uq_telegram_update_receipts_update_id"),
        Index(
            "ix_telegram_update_receipts_user_window",
            "user_id",
            "recorded_at",
            postgresql_where=text("disposition = 'accepted'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
