"""Checkpoint de alerta por Mission+Product (TASK-113, §33.4-§33.6).

`MissionOfferRelevance` (`(mission_id, offer_id)`) foi auditada e
descartada como base para este checkpoint: `offer_id` é por LOJA -- o
mesmo `Product` vendido em duas lojas diferentes (ex.: KaBuM e Amazon)
tem `Offer`s diferentes, o que reproduziria exatamente o bug original
(alerta repetido porque a loja nova "não sabia" do melhor preço já
alertado pela loja antiga). `MissionProductSelection` também foi
descartada -- é uma tabela de SELEÇÃO explícita de variante (modo
`SELECTED`), sem linha para os demais modos e com ciclo de vida
vinculado à escolha do usuário, não ao histórico de alertas.

Chave `(mission_id, product_id)`: mesmo `Product` em lojas diferentes
DENTRO da mesma Mission compartilha o checkpoint; outra variante
(`Product` diferente) ou outra Mission são sempre independentes. Ver
`app.alerts.evaluator` para a decisão A/B/C que lê/escreve este estado
sob lock (§33.5) e `app.collection.orchestration._persist_phase_c` para
o ponto de escrita, na mesma transação do `Event` de alerta (§33.21).
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CHAR, DateTime, ForeignKey, Index, Numeric, func
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class MissionProductAlertState(Base):
    __tablename__ = "mission_product_alert_state"
    __table_args__ = (
        Index("ix_mission_product_alert_state_product_id", "product_id"),
    )

    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    best_notified_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4), nullable=False
    )
    """Melhor preço de TODOS os alertas já enviados para esta Mission+
    Product -- nunca decresce por conta própria, só é atualizado para um
    valor menor (caminho B, §33.9) ou mantido (caminho C)."""
    best_notified_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    last_notified_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4), nullable=False
    )
    """Preço do alerta MAIS RECENTE (pode ser maior que
    `best_notified_amount` -- caminho C, re-alert de oportunidade sem
    bater o melhor histórico)."""
    last_notified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rearmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """REARM (§33.8): setado quando o preço sobe materialmente acima de
    `last_notified_amount` depois de um alerta. `NULL` = não rearmado
    -- caminho C nunca dispara sem isso, mesmo com janela de tempo
    vencida e mercado favorável."""
    last_alert_event_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
