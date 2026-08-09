"""Reivindicação transacional e registro append-only do consumo de eventos."""

from collections.abc import Collection
from datetime import datetime
from re import fullmatch
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.events.models import ConsumptionOutcome, Event, EventConsumptionAttempt


class EventConsumptionError(ValueError):
    """Indica entrada inválida para o contrato de consumo de eventos."""


def claim_unconsumed_events(
    session: Session,
    *,
    consumer_name: str,
    limit: int = 100,
    event_types: Collection[str] | None = None,
    max_attempts: int = 5,
    now: datetime | None = None,
) -> list[Event]:
    """Bloqueia eventos sem resultado terminal do consumidor na transação."""
    _validate_consumer_name(consumer_name)
    if not 1 <= limit <= 1000:
        raise EventConsumptionError("limit must be between 1 and 1000")
    if not 1 <= max_attempts <= 100:
        raise EventConsumptionError("max_attempts must be between 1 and 100")
    if now is not None:
        _require_aware(now)
    normalized_event_types = _validate_event_types(event_types)

    terminal = exists(
        select(EventConsumptionAttempt.id).where(
            EventConsumptionAttempt.event_id == Event.id,
            EventConsumptionAttempt.consumer_name == consumer_name,
            EventConsumptionAttempt.outcome.in_(
                (
                    ConsumptionOutcome.SUCCEEDED,
                    ConsumptionOutcome.SKIPPED,
                    ConsumptionOutcome.DEAD_LETTERED,
                )
            ),
        )
    )
    failed_count = (
        select(func.count(EventConsumptionAttempt.id))
        .where(
            EventConsumptionAttempt.event_id == Event.id,
            EventConsumptionAttempt.consumer_name == consumer_name,
            EventConsumptionAttempt.outcome == ConsumptionOutcome.FAILED,
        )
        .correlate(Event)
        .scalar_subquery()
    )
    retry_clock = now if now is not None else func.now()
    retry_pending = exists(
        select(EventConsumptionAttempt.id).where(
            EventConsumptionAttempt.event_id == Event.id,
            EventConsumptionAttempt.consumer_name == consumer_name,
            EventConsumptionAttempt.outcome == ConsumptionOutcome.FAILED,
            EventConsumptionAttempt.next_retry_at > retry_clock,
        )
    )
    statement = select(Event).where(
        ~terminal,
        failed_count < max_attempts,
        ~retry_pending,
    )
    if normalized_event_types is not None:
        statement = statement.where(Event.event_type.in_(normalized_event_types))
    statement = (
        statement.order_by(Event.recorded_at, Event.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Event)
    )
    return list(session.scalars(statement))


def _validate_event_types(
    event_types: Collection[str] | None,
) -> tuple[str, ...] | None:
    if event_types is None:
        return None
    if not event_types or any(
        not isinstance(event_type, str) or not event_type.strip()
        for event_type in event_types
    ):
        raise EventConsumptionError("event_types must contain non-blank strings")
    return tuple(sorted(set(event_types)))


def record_consumption_attempt(
    session: Session,
    *,
    event: Event,
    consumer_name: str,
    outcome: ConsumptionOutcome,
    attempted_at: datetime,
    failure_code: str | None = None,
    next_retry_at: datetime | None = None,
) -> EventConsumptionAttempt:
    """Registra o resultado sem decidir commit ou rollback do chamador."""
    if not isinstance(event, Event) or not isinstance(event.id, UUID):
        raise EventConsumptionError("event must be a persisted Event")
    _validate_consumer_name(consumer_name)
    if not isinstance(outcome, ConsumptionOutcome):
        raise EventConsumptionError("outcome must use ConsumptionOutcome")
    _require_aware(attempted_at)
    _validate_failure(outcome, failure_code, attempted_at, next_retry_at)

    attempt = EventConsumptionAttempt(
        event_id=event.id,
        consumer_name=consumer_name,
        outcome=outcome,
        failure_code=failure_code,
        attempted_at=attempted_at,
        next_retry_at=next_retry_at,
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


def _validate_failure(
    outcome: ConsumptionOutcome,
    failure_code: str | None,
    attempted_at: datetime,
    next_retry_at: datetime | None,
) -> None:
    if outcome in (ConsumptionOutcome.SUCCEEDED, ConsumptionOutcome.SKIPPED):
        if failure_code is not None or next_retry_at is not None:
            raise EventConsumptionError("terminal attempt cannot have failure_code")
        return
    if (
        not isinstance(failure_code, str)
        or len(failure_code) > 120
        or fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", failure_code) is None
    ):
        raise EventConsumptionError(
            "failed attempt requires a stable snake_case failure_code"
        )
    if outcome is ConsumptionOutcome.FAILED:
        if next_retry_at is None:
            raise EventConsumptionError("failed attempt requires next_retry_at")
        _require_aware(next_retry_at)
        if next_retry_at <= attempted_at:
            raise EventConsumptionError("next_retry_at must follow attempted_at")
    elif next_retry_at is not None:
        raise EventConsumptionError("dead-lettered attempt cannot have next_retry_at")


def count_failed_attempts(
    session: Session, *, event_id: UUID, consumer_name: str
) -> int:
    """Conta apenas fatos failed; terminais permanecem semanticamente separados."""
    _validate_consumer_name(consumer_name)
    count = session.scalar(
        select(func.count(EventConsumptionAttempt.id)).where(
            EventConsumptionAttempt.event_id == event_id,
            EventConsumptionAttempt.consumer_name == consumer_name,
            EventConsumptionAttempt.outcome == ConsumptionOutcome.FAILED,
        )
    )
    return int(count or 0)
