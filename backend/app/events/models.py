"""Modelo persistente e append-only de eventos de domínio."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Event(Base):
    """Fato de domínio durável, publicado para consumo desacoplado."""

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "btrim(event_type) <> ''",
            name="ck_events_event_type_not_blank",
        ),
        CheckConstraint(
            "btrim(aggregate_type) <> ''",
            name="ck_events_aggregate_type_not_blank",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_events_payload_object",
        ),
        Index(
            "ix_events_aggregate_history",
            "aggregate_type",
            "aggregate_id",
            "occurred_at",
            "id",
        ),
        Index(
            "ix_events_mission_history",
            "mission_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    mission_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("missions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ConsumptionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EventConsumptionAttempt(Base):
    """Resultado append-only de uma tentativa de consumo por consumidor."""

    __tablename__ = "event_consumption_attempts"
    __table_args__ = (
        CheckConstraint(
            "btrim(consumer_name) <> ''",
            name="ck_event_consumption_attempts_consumer_not_blank",
        ),
        CheckConstraint(
            "(outcome = 'succeeded' AND failure_code IS NULL) OR "
            "(outcome = 'failed' AND failure_code IS NOT NULL AND "
            "failure_code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$')",
            name="ck_event_consumption_attempts_outcome_failure",
        ),
        Index(
            "ix_event_consumption_attempts_success",
            "consumer_name",
            "event_id",
            postgresql_where=text("outcome = 'succeeded'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    consumer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[ConsumptionOutcome] = mapped_column(
        Enum(
            ConsumptionOutcome,
            name="consumption_outcome",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
