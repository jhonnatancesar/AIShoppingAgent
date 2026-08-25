"""Modelo persistente de missões e seu vocabulário de estados."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.time import utc_now


class MissionStatus(StrEnum):
    """Estados persistidos definidos pelo ciclo de vida da TASK-018."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class MissionCommand(StrEnum):
    """Comandos aceitos pelo ciclo de vida da missão."""

    ACTIVATE = "activate"
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETE = "complete"
    CANCEL = "cancel"
    EXPIRE = "expire"


class VariantSelectionMode(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    SELECTED = "selected"
    ALL = "all"


class Mission(Base):
    """Intenção de compra e fonte de verdade para seu estado atual."""

    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint("btrim(title) <> ''", name="ck_missions_title_not_blank"),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_missions_expiration_after_creation",
        ),
        CheckConstraint(
            "state_version >= 0",
            name="ck_missions_state_version_non_negative",
        ),
        Index(
            "ix_missions_user_status_created_at",
            "user_id",
            "status",
            desc("created_at"),
        ),
        Index(
            "ix_missions_pending_expiration",
            "status",
            "expires_at",
            postgresql_where=(
                "expires_at IS NOT NULL AND "
                "status NOT IN ('completed', 'cancelled', 'expired')"
            ),
        ),
        CheckConstraint(
            "(prelist_lowest_amount IS NULL AND prelist_lowest_currency IS NULL) OR "
            "(prelist_lowest_amount IS NOT NULL AND prelist_lowest_currency IS NOT NULL)",
            name="ck_missions_prelist_lowest_pair",
        ),
        CheckConstraint(
            "prelist_lowest_amount IS NULL OR prelist_lowest_amount >= 0",
            name="ck_missions_prelist_lowest_amount_non_negative",
        ),
        CheckConstraint(
            "prelist_lowest_currency IS NULL OR prelist_lowest_currency ~ '^[A-Z]{3}$'",
            name="ck_missions_prelist_lowest_currency_iso4217",
        ),
        CheckConstraint(
            "prelist_sent = true OR prelist_errata_sent = false",
            name="ck_missions_prelist_errata_requires_sent",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[MissionStatus] = mapped_column(
        Enum(
            MissionStatus,
            name="mission_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            validate_strings=True,
        ),
        nullable=False,
        default=MissionStatus.DRAFT,
        server_default=MissionStatus.DRAFT.value,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    state_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
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
    # TASK-068: pré-lista informativa (sem IA), enviada uma única vez após a
    # primeira rodada completa de coleta; `prelist_errata_sent` controla a
    # única mensagem de correção permitida se uma coleta posterior encontrar
    # algo mais barato que a base já enviada (`prelist_lowest_amount`).
    prelist_sent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    prelist_errata_sent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    prelist_lowest_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )
    prelist_lowest_currency: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )


class MissionCriteria(Base):
    """Critérios editáveis de busca e preço-alvo de uma missão."""

    __tablename__ = "mission_criteria"
    __table_args__ = (
        CheckConstraint(
            "btrim(search_query) <> ''",
            name="ck_mission_criteria_search_query_not_blank",
        ),
        CheckConstraint(
            "model IS NULL OR btrim(model) <> ''",
            name="ck_mission_criteria_model_not_blank",
        ),
        CheckConstraint(
            "target_amount IS NULL OR target_amount >= 0",
            name="ck_mission_criteria_target_amount_non_negative",
        ),
        CheckConstraint(
            "(target_amount IS NULL AND target_currency IS NULL) OR "
            "(target_amount IS NOT NULL AND target_currency IS NOT NULL)",
            name="ck_mission_criteria_target_pair",
        ),
        CheckConstraint(
            "target_currency IS NULL OR target_currency ~ '^[A-Z]{3}$'",
            name="ck_mission_criteria_currency_iso4217",
        ),
        CheckConstraint(
            "request_kind IN ('specific_product', 'product_family', 'generic_category')",
            name="ck_mission_criteria_request_kind_values",
        ),
        CheckConstraint(
            "variant_selection_mode IN ('not_required', 'pending', 'selected', 'all')",
            name="ck_mission_criteria_variant_selection_mode_values",
        ),
        CheckConstraint(
            "(request_kind = 'specific_product' AND requested_identity_key IS NOT NULL "
            "AND requested_family_key IS NOT NULL AND requested_variant IS NOT NULL "
            "AND variant_selection_mode = 'not_required') "
            "OR (request_kind = 'product_family' AND requested_identity_key IS NULL "
            "AND requested_family_key IS NOT NULL AND variant_selection_mode IN "
            "('pending', 'selected', 'all')) OR (request_kind = 'generic_category' "
            "AND requested_identity_key IS NULL AND requested_family_key IS NULL "
            "AND requested_variant IS NULL "
            "AND variant_selection_mode = 'not_required')",
            name="ck_mission_criteria_product_request_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    search_query: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """TASK-075: modelo/variante completo do produto (ex.: "9950X3D",
    "RTX 4070 Ti"), extraído pela mesma interpretação de IA que gera
    `search_query`. `None` quando não há modelo específico identificável
    com segurança -- nesse caso o filtro determinístico de coleta não
    descarta nada por modelo e a seleção de menor preço da Amazon não
    roda (identidade forte é pré-requisito)."""
    target_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )
    target_currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
    request_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="generic_category", server_default="generic_category"
    )
    requested_family_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requested_identity_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requested_variant: Mapped[str | None] = mapped_column(String(160), nullable=True)
    variant_selection_mode: Mapped[VariantSelectionMode] = mapped_column(
        Enum(
            VariantSelectionMode,
            name="variant_selection_mode_values",
            values_callable=lambda values: [value.value for value in values],
            validate_strings=True,
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
        default=VariantSelectionMode.NOT_REQUIRED,
        server_default=VariantSelectionMode.NOT_REQUIRED.value,
    )
    variant_prompted_at: Mapped[datetime | None] = mapped_column(
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


class MonitoringItem(Base):
    """Item de monitoramento compartilhável (TASK-112, fase 2).

    Nasce da `monitoring_key` resolvida pelo Product Identity Engine
    (fase 1, `app.products.identity.resolve_monitoring_identity`) --
    determinística, sem IA na decisão. Deliberadamente **não** é `Offer`
    (resultado de coleta, por loja) nem `Product` (identidade cross-loja
    da TASK-097, que só existe depois de alguma coleta real): existe
    desde a criação da missão, antes de qualquer coleta acontecer.
    """

    __tablename__ = "monitoring_items"
    __table_args__ = (
        CheckConstraint(
            "identity_version > 0", name="ck_monitoring_items_identity_version_positive"
        ),
        Index("uq_monitoring_items_monitoring_key", "monitoring_key", unique=True),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    monitoring_key: Mapped[str] = mapped_column(String(160), nullable=False)
    identity_version: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_identity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class MissionMonitoringItem(Base):
    """Vínculo N:1 entre `Mission` e `MonitoringItem` (TASK-112, fase 2).

    Uma missão só pertence a um item por vez -- `mission_id` é a própria
    chave primária, sem tabela de junção N:N. Deliberadamente sem
    estado/status próprio: se o vínculo "conta" para a necessidade real
    de coleta é sempre derivado de `Mission.status` no momento da
    consulta (`app.missions.monitoring`), nunca um flag redundante que
    precisaria ser mantido em dia à parte.
    """

    __tablename__ = "mission_monitoring_items"

    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    monitoring_item_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("monitoring_items.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )


class MonitoringItemStore(Base):
    """Necessidade real de coleta por `(item, loja)` (TASK-112, fase 2).

    Substitui, na granularidade compartilhada, o papel de agenda
    (`MissionSchedule`) e de backoff (`MissionSource.next_eligible_at`/
    `consecutive_blocks`, DEC-046) que hoje existem por missão.
    `MissionSource` continua existindo sem nenhuma mudança de forma --
    é a preferência do usuário e conta cota (TASK-107); esta tabela é só
    a necessidade AGREGADA de coleta entre todas as missões vinculadas.

    Nada aqui é lido por nenhum scheduler ainda (fase 3, fan-out/coleta
    compartilhada) -- só documenta a necessidade. `is_enabled` é mantido
    pelo lifecycle de missão (`app.missions.monitoring`): fica `True`
    enquanto ao menos uma missão `ACTIVE` vinculada ao mesmo item exigir
    esta loja, `False` quando a última sair -- histórico
    (`next_run_at`/`next_eligible_at`/`consecutive_blocks`) nunca é
    apagado, só para de ser avançado.
    """

    __tablename__ = "monitoring_item_stores"
    __table_args__ = (
        CheckConstraint(
            "consecutive_blocks >= 0",
            name="ck_monitoring_item_stores_consecutive_blocks_non_negative",
        ),
    )

    monitoring_item_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("monitoring_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_eligible_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_blocks: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class MissionTransition(Base):
    """Fato imutável que registra uma mudança aceita de estado."""

    __tablename__ = "mission_transitions"
    __table_args__ = (
        CheckConstraint(
            "command IN ('activate', 'pause', 'resume', 'complete', 'cancel', 'expire')",
            name="mission_command_values",
        ),
        CheckConstraint(
            "from_status <> to_status",
            name="ck_mission_transitions_status_changed",
        ),
        CheckConstraint(
            "btrim(actor_type) <> ''",
            name="ck_mission_transitions_actor_type_not_blank",
        ),
        CheckConstraint(
            "reason IS NULL OR btrim(reason) <> ''",
            name="ck_mission_transitions_reason_not_blank",
        ),
        Index(
            "ix_mission_transitions_history",
            "mission_id",
            "transitioned_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_status: Mapped[MissionStatus] = mapped_column(
        Enum(
            MissionStatus,
            name="mission_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            validate_strings=True,
            create_constraint=False,
        ),
        nullable=False,
    )
    to_status: Mapped[MissionStatus] = mapped_column(
        Enum(
            MissionStatus,
            name="mission_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            validate_strings=True,
            create_constraint=False,
        ),
        nullable=False,
    )
    command: Mapped[MissionCommand] = mapped_column(
        Enum(
            MissionCommand,
            name="mission_command_values",
            values_callable=lambda commands: [command.value for command in commands],
            validate_strings=True,
            native_enum=False,
            create_constraint=False,
            length=32,
        ),
        nullable=False,
    )
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    transitioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class MissionSource(Base):
    """Fonte explicitamente selecionada para a busca de uma missão.

    `next_eligible_at`/`consecutive_blocks` (DEC-046) implementam backoff
    persistente por `(mission_id, store_id)` após bloqueio externo
    confirmado (401/403/429): não afetam a seleção da fonte nem a agenda
    da missão, só se essa fonte específica pode ser reivindicada agora.
    """

    __tablename__ = "mission_sources"
    __table_args__ = (
        Index("ix_mission_sources_store_id", "store_id"),
        CheckConstraint(
            "consecutive_blocks >= 0",
            name="ck_mission_sources_consecutive_blocks_non_negative",
        ),
    )

    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    store_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("stores.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    next_eligible_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_blocks: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class MissionProductSelection(Base):
    """Variante global explicitamente escolhida para uma missão de família."""

    __tablename__ = "mission_product_selections"

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now()
    )


class MissionSchedule(Base):
    """Agenda recorrente e editável de uma missão."""

    __tablename__ = "mission_schedules"
    __table_args__ = (
        CheckConstraint(
            "interval_minutes > 0",
            name="ck_mission_schedules_interval_positive",
        ),
        CheckConstraint(
            "last_run_at IS NULL OR last_run_at <= next_run_at",
            name="ck_mission_schedules_run_order",
        ),
        Index(
            "ix_mission_schedules_due",
            "next_run_at",
            "mission_id",
            postgresql_where="is_enabled",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mission_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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
