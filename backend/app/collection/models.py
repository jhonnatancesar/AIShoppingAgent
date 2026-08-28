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
    Float,
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
        CheckConstraint(
            "(mission_id IS NOT NULL AND monitoring_item_id IS NULL) OR "
            "(mission_id IS NULL AND monitoring_item_id IS NOT NULL)",
            name="ck_collection_runs_ownership_xor",
        ),
        CheckConstraint(
            "fairness_owner_user_id IS NULL OR monitoring_item_id IS NOT NULL",
            name="ck_collection_runs_fairness_owner_requires_shared",
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
        Index(
            "uq_collection_runs_running_monitoring_item_store",
            "monitoring_item_id",
            "store_id",
            unique=True,
            postgresql_where="status = 'running' AND monitoring_item_id IS NOT NULL",
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
    monitoring_item_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("monitoring_items.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """TASK-112 (fase 3A): execução compartilhada por (item, loja) -- claim
    persistente via a mesma técnica já usada por `mission_id`
    (`uq_collection_runs_running_*`, IntegrityError na corrida = perdeu o
    claim). `mission_id` continua `NULL` numa run compartilhada -- nenhuma
    `PriceObservation` é gravada presa a ela; cada Mission elegível do
    fan-out ganha sua PRÓPRIA run (`mission_id` preenchido,
    `monitoring_item_id` nulo), exatamente como hoje."""
    fairness_owner_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """TASK-112 (fase 3B): usuário que consumiu o turno de fairness desta
    run compartilhada -- trilha de auditoria numa linha só (CollectionRun
    -> monitoring_item_id -> store_id -> fairness_owner_user_id), sem
    join extra. Gravado na MESMA transação/savepoint do claim
    (`app.collection.shared_claim._claim_shared_collection_in_session`)
    -- se a aquisição do turno falhar logo em seguida, o rollback do
    savepoint desfaz a CollectionRun inteira, atribuição incluída.
    `NULL` em duas situações diferentes: (a) sempre `NULL` numa run do
    caminho antigo (`monitoring_item_id IS NULL`, reforçado pelo CHECK
    abaixo); (b) pode ser `NULL` numa run compartilhada disparada FORA do
    scheduler (`collect_monitoring_item_store` chamada direto -- script/
    ADMIN/teste, sem passar `fairness_owner_user_id`) -- significa
    "execução shared fora da fila de fairness", nunca "o scheduler
    esqueceu de gravar o dono". No caminho do `CollectionOrchestrator`
    (`claim_due_work`), owner `NULL` é impossível por construção -- só
    reserva um alvo compartilhado para donos já resolvidos pela Fase 1 de
    `app.collection.fairness._reserve_fairness_owners`. `ondelete=
    RESTRICT` (não `SET NULL`): `app.privacy.service.deidentify_account`
    nunca apaga a linha `User`, só remove credenciais/`telegram_user_id`
    -- a FK nunca fica pendurada na prática; `RESTRICT` é só consistência
    com o resto do histórico append-only do projeto."""
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
    last_observation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("price_observations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """TASK-112 (fase 3A): última `PriceObservation` que esta Mission já
    processou para esta oferta -- fonte de "previous" (DEC-048) na coleta
    compartilhada, onde `PriceObservation.collection_run_id` aponta para a
    `CollectionRun` do `MonitoringItem` (sem `mission_id`), nunca para uma
    run de missão específica. Mantido também no caminho de missão única
    (populado por `_persist_phase_c`), sem mudar nenhum comportamento
    existente lá -- ninguém mais lê este campo fora do fan-out
    compartilhado (`app.collection.shared_collection`)."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class SharedCollectionOffer(Base):
    """Registro durável de quais `Offer`s fizeram parte de UMA coleta
    compartilhada (TASK-112, fase 3A, correção "fan-out durável").

    Existe para que, se o processo morrer depois que a persistência
    comercial já commitou mas antes do fan-out terminar, seja possível
    reconstruir o resultado da coleta (`offer_id`/`observation_id` por
    `collection_run_id`) SEM chamar o provider de novo -- inclusive
    quando a `PriceObservation` foi reaproveitada (redundante, TASK-093)
    e por isso não está presa a esta `collection_run_id` via
    `PriceObservation.collection_run_id`. Nunca apagado; uma
    `CollectionRun` (compartilhada) nunca é reprocessada depois de
    `SUCCEEDED`.
    """

    __tablename__ = "shared_collection_offers"

    collection_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    offer_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    observation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("price_observations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class SharedFanOutStatus(StrEnum):
    """Máquina de estados de `SharedFanOutTask` (TASK-112, fase 3A,
    correção de consistência -- rodada de classificação de erro).

    `PENDING`: elegível para ser reivindicada -- nunca tentada ainda, OU
    já tentada e liberada para nova tentativa (`next_retry_at` no
    passado/nulo). `PROCESSING`: reivindicada por um worker
    (`claimed_at` marca o início do lease) -- nunca dois workers
    processam a mesma tarefa ao mesmo tempo (claim atômico via `UPDATE
    ... WHERE status='pending'`, nunca mutex em memória); uma
    `PROCESSING` presa além do timeout de lease é stale, recuperável via
    `recover_stale_fan_out_tasks` (mesmo espírito de `recover_stale_
    runs`). `DONE`: terminal, sucesso -- nunca mais reprocessada.

    `SKIPPED`: terminal, mas NUNCA um erro -- a Mission deixou de ser
    elegível para este fan-out entre a coleta comercial e o
    processamento (pausada/cancelada/desvinculada do `MonitoringItem`/
    perdeu a loja) -- revalidada deterministicamente ANTES de processar
    (nunca depois de já ter alertado). Nunca notifica.

    Falha de PROCESSAMENTO se divide em duas categorias -- nunca uma
    falha transitória vira perda silenciosa só por ter acontecido
    `_MAX_FAN_OUT_ATTEMPTS` vezes:

    `ATTENTION_REQUIRED`: erro RETRYABLE (banco temporariamente
    indisponível, timeout, IA/provider auxiliar indisponível, etc.) que
    esgotou as tentativas automáticas (backoff via `next_retry_at`/
    `attempt_count`) -- para de tentar sozinho (evita loop infinito), mas
    continua auditável (`last_error`) e reprocessável manualmente (nada
    no schema impede resetar para `pending`); nunca é a mesma coisa que
    "definitivamente impossível".

    `TERMINAL_FAILED`: erro determinístico (`SharedFanOutTerminalError`)
    -- dados/estado tornam o processamento genuinamente impossível
    (ex.: `MissionCriteria` não existe mais para a Mission reivindicada).
    Vai direto para cá, sem gastar tentativas de retry (retry nunca
    resolveria), mas continua auditável via `last_error`.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    SKIPPED = "skipped"
    ATTENTION_REQUIRED = "attention_required"
    TERMINAL_FAILED = "terminal_failed"


class SharedFanOutTask(Base):
    """Necessidade durável de UMA Mission processar o resultado de UMA
    coleta compartilhada (TASK-112, fase 3A, correção "fan-out durável"
    + correção de consistência de retry/concorrência/lease).

    Criada atomicamente junto da persistência comercial (mesma transação
    que grava `SharedCollectionOffer` e marca a `CollectionRun`
    `SUCCEEDED`) -- por isso, assim que a coleta comercial está
    persistida, o banco já sabe exatamente quais Missions ainda precisam
    processá-la. Um crash a qualquer momento depois disso nunca perde
    essa informação: `app.collection.shared_collection.
    resume_shared_collection_fan_out` retoma só tarefas elegíveis
    (`pending` due, ou `processing` stale via recovery), nunca reprocessa
    uma já `done`, nunca chama o provider de novo.

    `attempt_count`/`last_error`/`next_retry_at` implementam retry com
    backoff (`_MAX_FAN_OUT_ATTEMPTS`, `_fan_out_retry_delay_minutes`) --
    falha transitória de banco/IA/processamento NUNCA fica `pending`
    para sempre nem vira perda silenciosa: tenta de novo com atraso
    crescente, e só depois de esgotar as tentativas fica
    `terminal_failed` (auditável via `last_error`, nunca reprocessada
    automaticamente a partir daí)."""

    __tablename__ = "shared_fan_out_tasks"
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_shared_fan_out_tasks_attempt_count_non_negative",
        ),
        Index(
            "ix_shared_fan_out_tasks_pending",
            "collection_run_id",
            postgresql_where="status = 'pending'",
        ),
        Index(
            "ix_shared_fan_out_tasks_processing_claimed_at",
            "claimed_at",
            postgresql_where="status = 'processing'",
        ),
    )

    collection_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    status: Mapped[SharedFanOutStatus] = mapped_column(
        Enum(
            SharedFanOutStatus,
            name="shared_fan_out_status",
            values_callable=lambda values: [v.value for v in values],
        ),
        nullable=False,
        default=SharedFanOutStatus.PENDING,
        server_default=SharedFanOutStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
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
    last_fairness_turn_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    """TASK-112 (fase 3B): rastro de auditoria/depuração -- "qual ciclo
    (`turn_id`, um `uuid4()` sorteado por chamada de `run_batch`) creditou
    o último avanço deste usuário". NÃO é o mecanismo de exclusão mútua
    entre execuções concorrentes (isso é o lock contínuo de linha
    adquirido por `app.collection.fairness._reserve_fairness_owners`,
    mantido até o commit) -- comparar tokens por igualdade sozinho já foi
    tentado e rejeitado (não impede duas execuções com `now` diferentes
    de creditarem o mesmo usuário, ver `docs/tasks/TASK-112.md`)."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class StoreThrottleState(Base):
    """Pacing GLOBAL por loja (TASK-108) -- camada distinta do backoff
    por `(mission_id, store_id)` de `MissionSource` (`DEC-046`, que
    continua intocado). Aqui é global entre TODOS os usuários/missões:
    nenhuma troca de usuário pode "furar" o intervalo mínimo entre
    requisições à mesma loja, porque do ponto de vista da própria loja é
    sempre o mesmo worker/IP fazendo a requisição, não importa de qual
    usuário partiu. Auditoria confirmou que nada equivalente existia
    antes (o circuit breaker de `app.core.resilience` é global por
    provider, mas só em memória, perdido a cada restart do worker)."""

    __tablename__ = "store_throttle_state"

    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        primary_key=True,
    )
    next_allowed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class CollectionQueueConfig(Base):
    """Configuração da fila justa/pacing global, persistida e editável
    pelo ADMIN (TASK-108) -- linha única (`id` fixo em 1). `NULL` em
    qualquer campo usa o default de `Settings`
    (`AISHOPPING_MAX_CONCURRENT_USER_BATCHES`/etc.), mesmo padrão já
    usado pelos overrides de cota por usuário (TASK-107,
    `resolve_quota_limits`). Lida de novo a cada `run_batch`
    (`resolve_queue_config`) -- uma mudança pelo ADMIN nunca precisa de
    restart do worker para valer."""

    __tablename__ = "collection_queue_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_collection_queue_config_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    max_concurrent_user_batches_override: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    user_cooldown_min_seconds_override: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    user_cooldown_max_seconds_override: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    store_min_interval_seconds_override: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class PromotionalWindow(Base):
    """Janela de cadência promocional determinística (TASK-112, fase 3B)
    -- dado, não código: ADMIN insere/remove linhas para calendário
    promocional (datas duplas -- 9/9, 10/10, 11/11, 12/12 --, Black
    Friday, Cyber Monday, outras futuras) sem NENHUMA migration nova.
    Enquanto `now` cai dentro de QUALQUER janela ativa, a política de
    cadência (`app.collection.cadence`) usa a faixa promocional para toda
    loja -- nunca substitui fairness/StoreThrottle/backoff, só torna
    `MonitoringItemStore.next_run_at` due mais cedo (`app.collection.
    cadence.resolve_collection_cadence`)."""

    __tablename__ = "promotional_windows"
    __table_args__ = (
        CheckConstraint(
            "ends_at > starts_at", name="ck_promotional_windows_time_order"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class StoreActivityState(Base):
    """Estado mínimo por loja para detecção determinística de atividade
    comercial alta (TASK-112, fase 3B). A CONTAGEM de mudanças em si
    nunca é cacheada aqui -- é sempre recomputada ao vivo a partir de
    `PriceObservation` (TASK-093/DEC-097: uma nova linha só existe quando
    o estado comercial de fato mudou, sinal já durável, sem schema novo
    para a contagem em si -- ver `app.collection.cadence._is_high_
    activity`). Esta tabela guarda só a HISTERESE: uma vez detectado um
    pico (quantidade de mudanças >= limiar dentro da janela de
    observação, ambos configuráveis), `high_activity_until` mantém a loja
    em modo acelerado por uma duração mínima configurável mesmo que as
    mudanças que dispararam o pico já tenham saído da janela de
    observação no ciclo seguinte -- evita alternar NORMAL/HIGH_ACTIVITY a
    cada ciclo bem na borda do limiar. Sem `high_activity_until` no
    futuro (nunca disparado, ou já expirado sem um novo pico), a loja
    está em NORMAL -- nenhuma transição explícita de saída é necessária,
    só a passagem do tempo."""

    __tablename__ = "store_activity_state"

    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    """TASK-116: unidade de monitoramento (`MonitoringItem.id` no caminho
    compartilhado, `Mission.id` no caminho legado sem `MonitoringItem`) --
    nunca FK direta, já que aponta para uma de duas tabelas diferentes
    conforme o caminho; a integridade real é garantida por quem grava
    (sempre um `scope_id` que já existe em `monitoring_items` ou
    `missions` no momento da escrita)."""
    high_activity_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
