"""Avaliação de mercado externa por `Product` (TASK-113, §33.1-§33.3).

`MarketPriceAssessment` é uma única linha MUTÁVEL por `product_id`
(nunca histórico append-only) -- chave aprovada em §33.1: `Product.
identity_key IS NOT NULL` é pré-condição para disparar a pesquisa (fora
deste modelo, na camada de serviço), nunca `MonitoringItem` (identidade
de NECESSIDADE, deliberadamente mais ampla, pode agrupar N `Product`s
diferentes -- ver `docs/tasks/TASK-113.md` §33.1).

`status`/`lease_until`/`retry_after`/`failure_count` implementam o
single-flight crash-safe de §33.3 (claim atômico por `INSERT ... ON
CONFLICT ... DO UPDATE ... RETURNING`, mesmo espírito de
`SharedFanOutTask`/`StoreActivityState`, nunca `SELECT ... FOR UPDATE`
numa linha presumida existente). Vigência é decidida em CÓDIGO
(`status == READY AND expires_at > now()`), nunca por índice parcial
baseado em `expires_at` (§33.2).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class MarketAssessmentStatus(StrEnum):
    """Estados do single-flight (§33.3).

    `PENDING` nunca é observado persistido em produção (o claim já nasce
    `PROCESSING`, corrigido de uma versão anterior do desenho que
    permitia uma linha `pending` sem dono -- ver TASK-113 §33.3, ponto 1
    da rodada de correção pós-plano); mantido no enum só como estado
    inicial teoricamente válido do tipo, nunca produzido pelo claim
    atômico real."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class MarketPriceClassification(StrEnum):
    EXCELLENT_DEAL = "excellent_deal"
    GOOD_DEAL = "good_deal"
    NORMAL_PRICE = "normal_price"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AssessmentConfidence(StrEnum):
    """Aplica-se só à classificação de mercado atual (`market_low`/
    `market_high`), nunca a `historical_low_external` -- esse é sempre
    apresentado com sua própria fonte/data, nunca com confiança agregada
    (§33.2/§33.16)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MarketPriceAssessment(Base):
    """Uma linha corrente por `product_id` -- ver docstring do módulo."""

    __tablename__ = "market_price_assessments"
    __table_args__ = (
        CheckConstraint(
            "historical_low_source IS NOT NULL OR historical_low_external IS NULL",
            name="ck_market_price_assessments_historical_low_source_required",
        ),
        CheckConstraint(
            "failure_count >= 0",
            name="ck_market_price_assessments_failure_count_non_negative",
        ),
        Index(
            "ix_market_price_assessments_processing_lease",
            "lease_until",
            postgresql_where="status = 'processing'",
        ),
    )

    product_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[MarketAssessmentStatus] = mapped_column(
        Enum(
            MarketAssessmentStatus,
            name="market_assessment_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=MarketAssessmentStatus.PROCESSING,
        server_default=MarketAssessmentStatus.PROCESSING.value,
    )
    reference_price: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    reference_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    store_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """Só auditoria/contexto -- qual oferta disparou a última pesquisa.
    Nunca parte da chave (§33.1: chave é `product_id` sozinho, cross-loja
    por natureza)."""
    classification: Mapped[MarketPriceClassification | None] = mapped_column(
        Enum(
            MarketPriceClassification,
            name="market_price_classification",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=True,
    )
    market_low: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    market_high: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    historical_low_external: Mapped[Decimal | None] = mapped_column(
        Numeric(19, 4), nullable=True
    )
    historical_low_source: Mapped[str | None] = mapped_column(
        String(2000), nullable=True
    )
    historical_low_observed_at: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    confidence: Mapped[AssessmentConfidence | None] = mapped_column(
        Enum(
            AssessmentConfidence,
            name="assessment_confidence",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=True,
    )
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    """Resultados Firecrawl usados (title/url/description das duas
    buscas, §33.18) + resposta estruturada da IA -- rastreabilidade sem
    histórico append-only separado (§33.2)."""
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
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
