"""Testes do consumo transacional e append-only de eventos (TASK-044)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.events import (
    AggregateType,
    ConsumptionOutcome,
    Event,
    EventConsumptionAttempt,
    EventConsumptionError,
    claim_unconsumed_events,
    record_consumption_attempt,
)
from sqlalchemy import CheckConstraint, ForeignKeyConstraint

NOW = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)


def _event() -> Event:
    return Event(
        id=uuid4(),
        event_type="price.decreased.v1",
        aggregate_type=AggregateType.OFFER.value,
        aggregate_id=uuid4(),
        payload={},
        occurred_at=NOW,
        recorded_at=NOW,
    )


def test_consumption_attempt_table_matches_contract() -> None:
    table = EventConsumptionAttempt.__table__
    assert [column.name for column in table.columns] == [
        "id",
        "event_id",
        "consumer_name",
        "outcome",
        "failure_code",
        "attempted_at",
        "next_retry_at",
    ]
    assert table.c.outcome.type.name == "consumption_outcome"
    assert table.c.attempted_at.type.timezone is True
    assert {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        "ck_event_consumption_attempts_consumer_not_blank",
        "ck_event_consumption_attempts_outcome_failure",
    }
    foreign_key = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )
    element = tuple(foreign_key.elements)[0]
    assert element.target_fullname == "events.id"
    assert element.ondelete == "RESTRICT"
    index = next(index for index in table.indexes if index.unique)
    assert index.name == "ux_event_consumption_attempts_terminal"
    assert tuple(column.name for column in index.columns) == (
        "consumer_name",
        "event_id",
    )
    assert str(index.dialect_options["postgresql"]["where"]) == (
        "outcome IN ('succeeded', 'skipped', 'dead_lettered')"
    )


def test_consumption_attempt_is_registered_in_shared_metadata() -> None:
    assert EventConsumptionAttempt in REGISTERED_MODELS
    assert (
        Base.metadata.tables["event_consumption_attempts"]
        is EventConsumptionAttempt.__table__
    )


def test_claim_returns_deterministic_locked_events() -> None:
    event = _event()
    session = MagicMock()
    session.scalars.return_value = [event]

    result = claim_unconsumed_events(
        session, consumer_name="telegram_notifications", limit=25
    )

    assert result == [event]
    statement = session.scalars.call_args.args[0]
    rendered = str(statement)
    assert "event_consumption_attempts" in rendered
    assert "NOT (EXISTS" in rendered
    terminal_values = next(
        value
        for value in statement.compile().params.values()
        if isinstance(value, list)
    )
    assert ConsumptionOutcome.DEAD_LETTERED in terminal_values
    assert "next_retry_at" in rendered
    assert "ORDER BY events.recorded_at, events.id" in rendered
    assert statement._for_update_arg.skip_locked is True
    assert statement._for_update_arg.of == [Event.__table__]


def test_claim_can_filter_event_types_for_a_concrete_consumer() -> None:
    session = MagicMock()
    session.scalars.return_value = []

    claim_unconsumed_events(
        session,
        consumer_name="telegram_price_alerts_v1",
        event_types={"price.target_reached.v1", "price.decreased.v1"},
    )

    rendered = str(session.scalars.call_args.args[0])
    assert "events.event_type IN" in rendered


@pytest.mark.parametrize("event_types", [set(), {""}])
def test_claim_rejects_invalid_event_type_filter(event_types: set[str]) -> None:
    with pytest.raises(EventConsumptionError, match="event_types"):
        claim_unconsumed_events(
            MagicMock(), consumer_name="consumer", event_types=event_types
        )


@pytest.mark.parametrize("limit", [0, 1001])
def test_claim_rejects_invalid_limit(limit: int) -> None:
    with pytest.raises(EventConsumptionError, match="limit"):
        claim_unconsumed_events(
            MagicMock(), consumer_name="telegram_notifications", limit=limit
        )


@pytest.mark.parametrize("consumer_name", ["", "   ", "x" * 65])
def test_consumption_rejects_invalid_consumer_name(consumer_name: str) -> None:
    with pytest.raises(EventConsumptionError, match="consumer_name"):
        claim_unconsumed_events(MagicMock(), consumer_name=consumer_name)
    with pytest.raises(EventConsumptionError, match="consumer_name"):
        record_consumption_attempt(
            MagicMock(),
            event=_event(),
            consumer_name=consumer_name,
            outcome=ConsumptionOutcome.SUCCEEDED,
            attempted_at=NOW,
        )


@pytest.mark.parametrize(
    ("outcome", "failure_code"),
    [
        (ConsumptionOutcome.SUCCEEDED, "unexpected_error"),
        (ConsumptionOutcome.SKIPPED, "preference_disabled"),
        (ConsumptionOutcome.FAILED, None),
        (ConsumptionOutcome.FAILED, "Invalid code"),
        (ConsumptionOutcome.FAILED, "x" * 121),
    ],
)
def test_record_rejects_inconsistent_failure(
    outcome: ConsumptionOutcome, failure_code: str | None
) -> None:
    with pytest.raises(EventConsumptionError, match="failure_code"):
        record_consumption_attempt(
            MagicMock(),
            event=_event(),
            consumer_name="telegram_notifications",
            outcome=outcome,
            attempted_at=NOW,
            failure_code=failure_code,
        )


def test_record_rejects_invalid_event_outcome_and_time() -> None:
    session = MagicMock()
    with pytest.raises(EventConsumptionError, match="persisted Event"):
        record_consumption_attempt(
            session,
            event=Event(),
            consumer_name="consumer",
            outcome=ConsumptionOutcome.SUCCEEDED,
            attempted_at=NOW,
        )
    with pytest.raises(EventConsumptionError, match="ConsumptionOutcome"):
        record_consumption_attempt(
            session,
            event=_event(),
            consumer_name="consumer",
            outcome="succeeded",  # type: ignore[arg-type]
            attempted_at=NOW,
        )
    with pytest.raises(EventConsumptionError, match="timezone-aware"):
        record_consumption_attempt(
            session,
            event=_event(),
            consumer_name="consumer",
            outcome=ConsumptionOutcome.SUCCEEDED,
            attempted_at=datetime(2026, 8, 8, 18, 0),
        )
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("outcome", "failure_code"),
    [
        (ConsumptionOutcome.SUCCEEDED, None),
        (ConsumptionOutcome.FAILED, "delivery_timeout"),
        (ConsumptionOutcome.SKIPPED, None),
    ],
)
def test_record_adds_and_flushes_append_only_attempt(
    outcome: ConsumptionOutcome, failure_code: str | None
) -> None:
    session = MagicMock()
    event = _event()

    next_retry_at = (
        datetime(2026, 8, 8, 18, 1, tzinfo=UTC)
        if outcome is ConsumptionOutcome.FAILED
        else None
    )
    attempt = record_consumption_attempt(
        session,
        event=event,
        consumer_name="telegram_notifications",
        outcome=outcome,
        attempted_at=NOW,
        failure_code=failure_code,
        next_retry_at=next_retry_at,
    )

    assert attempt.event_id == event.id
    assert attempt.consumer_name == "telegram_notifications"
    assert attempt.outcome is outcome
    assert attempt.failure_code == failure_code
    assert attempt.attempted_at == NOW
    assert attempt.next_retry_at == next_retry_at
    session.add.assert_called_once_with(attempt)
    session.flush.assert_called_once_with()
