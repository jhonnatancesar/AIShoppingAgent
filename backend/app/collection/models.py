"""Persistência das execuções rastreáveis de coleta."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.collection.contracts import (
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
)
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
from app.database.base import Base
from app.database.time import utc_now


class CollectionRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR (status IN ('succeeded', 'failed') AND finished_at IS NOT NULL)",
            name="ck_collection_runs_terminal_finished",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_collection_runs_time_order",
        ),
        Index(
            "ix_collection_runs_mission_started_at", "mission_id", desc("started_at")
        ),
        Index("ix_collection_runs_store_started_at", "store_id", desc("started_at")),
        Index(
            "uq_collection_runs_running_mission_store",
            "mission_id",
            "store_id",
            unique=True,
            postgresql_where="status = 'running' AND mission_id IS NOT NULL",
        ),
    )
    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mission_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[CollectionRunStatus] = mapped_column(
        Enum(
            CollectionRunStatus,
            name="collection_run_status",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
        default=CollectionRunStatus.RUNNING,
        server_default=CollectionRunStatus.RUNNING.value,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class PriceObservation(Base):
    __tablename__ = "price_observations"
    __table_args__ = (
        CheckConstraint(
            "amount >= 0 AND (shipping_amount IS NULL OR shipping_amount >= 0)",
            name="ck_price_observations_amounts_non_negative",
        ),
        CheckConstraint(
            "total_amount = amount + COALESCE(shipping_amount, 0)",
            name="ck_price_observations_total_exact",
        ),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_price_observations_currency_iso4217"
        ),
        CheckConstraint(
            "seller_kind IS NULL OR seller_kind IN "
            "('platform', 'marketplace_partner', 'unknown')",
            name="ck_price_observations_seller_kind_values",
        ),
        CheckConstraint(
            "fulfillment_kind IS NULL OR fulfillment_kind IN "
            "('platform', 'marketplace_partner', 'unknown')",
            name="ck_price_observations_fulfillment_kind_values",
        ),
        CheckConstraint(
            "condition IN ('new', 'refurbished', 'used', 'unknown')",
            name="ck_price_observations_condition_values",
        ),
        Index(
            "ix_price_observations_offer_observed",
            "offer_id",
            desc("observed_at"),
            "id",
        ),
        Index("ix_price_observations_collection_run_id", "collection_run_id"),
        Index(
            "uq_price_observations_run_offer",
            "collection_run_id",
            "offer_id",
            unique=True,
        ),
    )
    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    collection_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    shipping_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(19, 4), nullable=True
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    fulfillment: Mapped[str | None] = mapped_column(String(120), nullable=True)
    seller_kind: Mapped[MarketplacePartyKind | None] = mapped_column(
        Enum(
            MarketplacePartyKind,
            name="marketplace_party_kind",
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=True,
    )
    fulfillment_kind: Mapped[MarketplacePartyKind | None] = mapped_column(
        Enum(
            MarketplacePartyKind,
            name="marketplace_party_kind",
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=True,
    )
    condition: Mapped[OfferCondition] = mapped_column(
        Enum(
            OfferCondition,
            name="offer_condition",
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            create_constraint=False,
            length=16,
        ),
        nullable=False,
        default=OfferCondition.UNKNOWN,
        server_default=OfferCondition.UNKNOWN.value,
    )
    availability: Mapped[Availability] = mapped_column(
        Enum(
            Availability,
            name="offer_availability",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    raw_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class OfferInstallmentOption(Base):
    """TASK-089 (DEC-069): uma condição de parcelamento apresentada pela
    loja no momento de uma `PriceObservation` específica -- relação 1:N,
    nunca campos escalares em `Offer`/`PriceObservation`, porque a
    investigação real confirmou que Pichau e Terabyte apresentam várias
    condições simultâneas por oferta (ex.: 1x-6x com desconto e 12x sem
    juros), não uma só.

    Vinculada a `price_observation_id` (não a `offer_id` direto) para
    herdar de graça a mesma semântica histórica/"estado atual" já usada
    por `PriceObservation`: a busca do "estado atual" é sempre pelas
    opções da observação mais recente daquela oferta (mesmo índice
    `ix_price_observations_offer_observed`), nunca por
    UPDATE/DELETE/flag -- consistente com o restante do projeto ser
    append-only. Opções que a loja deixou de oferecer simplesmente não
    aparecem mais na observação seguinte; as antigas permanecem no
    histórico, nunca apagadas.

    `installment_total_amount`/`discount_percent` continuam `NULL`
    sempre que a própria loja não rotular esse dado para esta opção
    específica -- nunca calculados (`installment_count *
    installment_amount` é proibido).

    Auditoria pós-implementação: `installment_amount`/`installment_total_amount`
    exigem `> 0` (não só `>= 0`, ao contrário de `PriceObservation.amount`,
    que aceita zero). Divergência deliberada, não inconsistência: o preço
    de um produto pode, em tese, ser zero (brinde/promoção); uma PARCELA
    ou um TOTAL PARCELADO de R$0,00 nunca representa uma condição
    comercial real -- só pode ser evidência de parsing quebrado, então o
    banco recusa antes de persistir lixo. `discount_percent` ganhou teto
    de 100 pela mesma razão: um desconto percentual acima de 100% nunca é
    um valor real capturado da loja, só sinal de campo errado.

    Extensão (apresentação Telegram): `is_highlighted` marca a opção que
    corresponde exatamente ao que a loja resumiu no card da busca -- é
    carimbada em `_installment_options_from_row`/`_merge_installment_options`
    (`providers/base.py`), nunca aqui; sem ela, depois do merge com a
    página individual não haveria como saber qual condição resumir numa
    notificação sem reabrir a página de novo. No máximo uma linha por
    `price_observation_id` deveria ficar `True` (o card só destaca uma
    condição por vez), mas isso não é reforçado por CHECK -- é uma
    garantia da camada de coleta, não do schema."""

    __tablename__ = "offer_installment_options"
    __table_args__ = (
        CheckConstraint(
            "installment_count > 0", name="ck_offer_installment_options_count_positive"
        ),
        CheckConstraint(
            "installment_amount > 0",
            name="ck_offer_installment_options_amount_positive",
        ),
        CheckConstraint(
            "installment_total_amount IS NULL OR installment_total_amount > 0",
            name="ck_offer_installment_options_total_positive",
        ),
        CheckConstraint(
            "discount_percent IS NULL OR "
            "(discount_percent >= 0 AND discount_percent <= 100)",
            name="ck_offer_installment_options_discount_range",
        ),
        CheckConstraint(
            "interest_kind IN ('interest_free', 'with_interest', 'unknown')",
            name="ck_offer_installment_options_interest_kind_values",
        ),
        UniqueConstraint(
            "price_observation_id",
            "installment_count",
            name="uq_offer_installment_options_observation_count",
        ),
        Index(
            "ix_offer_installment_options_price_observation_id",
            "price_observation_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    price_observation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("price_observations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    installment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    installment_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    installment_total_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(19, 4), nullable=True
    )
    discount_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    interest_kind: Mapped[InstallmentInterestKind] = mapped_column(
        Enum(
            InstallmentInterestKind,
            name="installment_interest_kind",
            values_callable=lambda values: [value.value for value in values],
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=InstallmentInterestKind.UNKNOWN,
        server_default=InstallmentInterestKind.UNKNOWN.value,
    )
    is_highlighted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )


class MissionOfferRelevance(Base):
    """Correspondência classificada por IA entre uma missão e uma oferta.

    Chave natural `(mission_id, offer_id)` (TASK-063): a mesma oferta pode
    ser `MATCH` para uma missão e `NO_MATCH` para outra, então a
    classificação nunca fica só em `Offer`/`Product`. Os insumos da
    classificação (busca da missão, título bruto da oferta) são imutáveis
    depois que a missão e a oferta existem — `MissionCriteria.search_query`
    nunca é editado e o título bruto de uma oferta já criada nunca muda
    (`Product.name`) — por isso uma linha aqui nunca precisa ser
    reclassificada; ela só é criada quando ainda não existe.
    """

    __tablename__ = "mission_offer_relevance"
    __table_args__ = (Index("ix_mission_offer_relevance_offer_id", "offer_id"),)

    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    classification: Mapped[OfferRelevance] = mapped_column(
        Enum(
            OfferRelevance,
            name="offer_relevance",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
    )
    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class UserCollectionQueueState(Base):
    """Estado da fila justa por usuário do `collection_worker` (TASK-108).

    Camada ortogonal ao backoff por provider (`MissionSource.
    next_eligible_at`, `DEC-046`) — esta aqui protege contra um único
    usuário monopolizar o worker, não contra bloqueio de uma loja.
    `last_processed_at=NULL` (usuário nunca processado) sempre vence no
    desempate round-robin. `next_eligible_at` é o cooldown individual:
    enquanto no futuro, o usuário fica de fora da seleção do próximo
    lote, mas nunca pausa a fila para os demais.
    """

    __tablename__ = "user_collection_queue_state"

    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_eligible_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
