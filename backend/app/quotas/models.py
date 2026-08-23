"""Contagem de pesquisas diárias por usuário (TASK-107)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class SearchReceipt(Base):
    """Um registro por pesquisa aceita -- mesmo princípio de
    `TelegramUpdateReceipt`: só uma pesquisa que passou na validação de
    entrada consome cota. `max_daily_searches` conta linhas deste usuário
    dentro do dia corrente (UTC, meia-noite a meia-noite)."""

    __tablename__ = "search_receipts"
    __table_args__ = (
        Index("ix_search_receipts_user_created_at", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
