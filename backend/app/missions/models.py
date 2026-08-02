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


class MissionCriteria(Base):
    """Critérios editáveis de busca e preço-alvo de uma missão."""

    __tablename__ = "mission_criteria"
    __table_args__ = (
        CheckConstraint(
            "btrim(search_query) <> ''",
            name="ck_mission_criteria_search_query_not_blank",
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
    target_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )
    target_currency: Mapped[str | None] = mapped_column(CHAR(3), nullable=True)
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


class MissionTransition(Base):
    """Fato imutável que registra uma mudança aceita de estado."""

    __tablename__ = "mission_transitions"
    __table_args__ = (
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
            create_constraint=True,
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
    """Fonte explicitamente selecionada para a busca de uma missão."""

    __tablename__ = "mission_sources"
    __table_args__ = (Index("ix_mission_sources_store_id", "store_id"),)

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
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
