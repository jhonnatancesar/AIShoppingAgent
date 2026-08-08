"""Reivindicação transacional e registro append-only do consumo de eventos."""

from datetime import datetime
from re import fullmatch
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.events.models import ConsumptionOutcome, Event, EventConsumptionAttempt


class EventConsumptionError(ValueError):
    """Indica entrada inválida para o contrato de consumo de eventos."""


def claim_unconsumed_events(
    session: Session,
    *,
    consumer_name: str,
    limit: int = 100,
) -> list[Event]:
    """Bloqueia eventos sem sucesso do consumidor na transação corrente."""
    _validate_consumer_name(consumer_name)
    if not 1 <= limit <= 1000:
        raise EventConsumptionError("limit must be between 1 and 1000")

    succeeded = exists(
        select(EventConsumptionAttempt.id).where(
            EventConsumptionAttempt.event_id == Event.id,
            EventConsumptionAttempt.consumer_name == consumer_name,
            EventConsumptionAttempt.outcome == ConsumptionOutcome.SUCCEEDED,
        )
    )
    statement = (
        select(Event)
        .where(~succeeded)
        .order_by(Event.recorded_at, Event.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Event)
    )
    return list(session.scalars(statement))


def record_consumption_attempt(
    session: Session,
    *,
    event: Event,
    consumer_name: str,
    outcome: ConsumptionOutcome,
    attempted_at: datetime,
    failure_code: str | None = None,
) -> EventConsumptionAttempt:
    """Registra o resultado sem decidir commit ou rollback do chamador."""
    if not isinstance(event, Event) or not isinstance(event.id, UUID):
        raise EventConsumptionError("event must be a persisted Event")
    _validate_consumer_name(consumer_name)
    if not isinstance(outcome, ConsumptionOutcome):
        raise EventConsumptionError("outcome must use ConsumptionOutcome")
    _require_aware(attempted_at)
    _validate_failure(outcome, failure_code)

    attempt = EventConsumptionAttempt(
        event_id=event.id,
        consumer_name=consumer_name,
        outcome=outcome,
        failure_code=failure_code,
        attempted_at=attempted_at,
    )
    session.add(attempt)
    session.flush()
    return attempt


def _validate_consumer_name(consumer_name: str) -> None:
    if (
        not isinstance(consumer_name, str)
        or not consumer_name.strip()
        or len(consumer_name) > 64
    ):
        raise EventConsumptionError(
            "consumer_name must be non-blank and at most 64 characters"
        )


def _require_aware(attempted_at: datetime) -> None:
    if not isinstance(attempted_at, datetime) or attempted_at.utcoffset() is None:
        raise EventConsumptionError("attempted_at must be timezone-aware")


def _validate_failure(outcome: ConsumptionOutcome, failure_code: str | None) -> None:
    if outcome is ConsumptionOutcome.SUCCEEDED:
        if failure_code is not None:
            raise EventConsumptionError("successful attempt cannot have failure_code")
        return
    if (
        not isinstance(failure_code, str)
        or len(failure_code) > 120
        or fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", failure_code) is None
    ):
        raise EventConsumptionError(
            "failed attempt requires a stable snake_case failure_code"
        )
