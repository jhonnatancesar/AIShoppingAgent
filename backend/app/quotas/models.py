"""Contagem de pesquisas diárias por usuário (TASK-107)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class SearchReceipt(Base):
    """Um registro por pesquisa aceita -- mesmo princípio de
    `TelegramUpdateReceipt`: só uma pesquisa que passou na validação de
    entrada consome cota. `max_daily_searches` conta linhas deste usuário
    dentro do dia corrente (UTC, meia-noite a meia-noite).

    `query_text` (Frente 5, 2026-09-12): o texto real pesquisado --
    `None` só para linhas gravadas antes desta coluna existir, nunca
    para linhas novas. É a fonte de verdade de "pesquisa efetivamente
    realizada" (inclusive sem missão nenhuma criada), usada pela view
    DEV "Minhas pesquisas"/"Todas as pesquisas" -- NUNCA pela view
    comunitária, que só enxerga produtos reconhecidos via
    `SearchReceiptProduct`, nunca texto livre do usuário."""

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
    query_text: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class SearchReceiptProduct(Base):
    """Liga uma pesquisa (`SearchReceipt`) a um `Product` RECONHECIDO
    (`identity_key IS NOT NULL`) que ela retornou -- só produtos com
    identidade resolvida entram aqui (nunca um resultado genérico/ainda
    não identificado). É esta tabela, não `SearchReceipt.query_text`,
    que alimenta "Veja o que estão pesquisando": mostrar só nomes de
    produto canônicos e vetados, nunca texto livre do usuário, é a
    proteção real contra expor conteúdo sensível -- exigir pluralidade
    de usuários distintos por si só não bastaria para texto livre."""

    __tablename__ = "search_receipt_products"
    __table_args__ = (
        Index(
            "uq_search_receipt_products_receipt_product",
            "search_receipt_id",
            "product_id",
            unique=True,
        ),
        Index("ix_search_receipt_products_product_id", "product_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    search_receipt_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("search_receipts.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
