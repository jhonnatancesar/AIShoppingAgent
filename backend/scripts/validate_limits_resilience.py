"""Validação PostgreSQL real e isolada dos contratos da TASK-049."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from time import perf_counter
from uuid import UUID, uuid4

from app.audit.models import AuditEntry
from app.core.config import Settings
from app.database.session import create_database_engine, create_session_factory
from app.events import (
    ConsumptionOutcome,
    Event,
    claim_unconsumed_events,
    record_consumption_attempt,
)
from app.missions.models import Mission, MissionStatus
from app.telegram.limits import reserve_telegram_update
from app.telegram.models import TelegramUpdateDisposition, TelegramUpdateReceipt
from app.users.models import User, UserRole
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError


def main() -> None:
    engine = create_database_engine(Settings())
    sessions = create_session_factory(engine)
    user_id = uuid4()
    run_suffix = uuid4().hex[:8]
    retry_consumer = f"task049_retry_{run_suffix}"
    race_consumer = f"task049_race_{run_suffix}"
    terminals_consumer = f"task049_terminals_{run_suffix}"
    validation_event_type = f"task049.validation.{run_suffix}"
    update_id = 9_000_000_000 + (uuid4().int % 100_000_000)
    with sessions.begin() as session:
        session.add(
            User(
                id=user_id,
                display_name="TASK-049 validation",
                role=UserRole.USER,
                telegram_user_id=update_id,
            )
        )

    barrier = Barrier(2)

    def process_same_update() -> bool:
        with sessions.begin() as session:
            barrier.wait(timeout=10)
            reservation = reserve_telegram_update(
                session,
                update_id=update_id,
                user_id=user_id,
                accepted_per_minute=20,
            )
            if reservation.replay:
                return False
            session.add(
                AuditEntry(
                    actor_type="task_validation",
                    actor_id=user_id,
                    action="task049.concurrent_effect",
                    resource_type="user",
                    resource_id=user_id,
                    entry_metadata={},
                )
            )
            return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        effects = list(executor.map(lambda _: process_same_update(), range(2)))
    assert sorted(effects) == [False, True]

    with sessions.begin() as session:
        receipt_count = session.scalar(
            select(func.count(TelegramUpdateReceipt.id)).where(
                TelegramUpdateReceipt.update_id == update_id
            )
        )
        effect_count = session.scalar(
            select(func.count(AuditEntry.id)).where(
                AuditEntry.action == "task049.concurrent_effect",
                AuditEntry.actor_id == user_id,
            )
        )
        assert receipt_count == effect_count == 1

        limited_update_id = update_id + 1
        limited = reserve_telegram_update(
            session,
            update_id=limited_update_id,
            user_id=user_id,
            accepted_per_minute=1,
        )
        assert limited.disposition is TelegramUpdateDisposition.RATE_LIMITED
    with sessions.begin() as session:
        replay = reserve_telegram_update(
            session,
            update_id=limited_update_id,
            user_id=user_id,
            accepted_per_minute=1,
        )
        assert replay.replay
        assert replay.disposition is TelegramUpdateDisposition.RATE_LIMITED

    now = datetime.now(UTC)
    mission_id = uuid4()
    event_id = uuid4()
    with sessions.begin() as session:
        mission = Mission(
            id=mission_id,
            user_id=user_id,
            title="TASK-049 retry",
            status=MissionStatus.ACTIVE,
        )
        session.add(mission)
        session.flush()
        event = Event(
            id=event_id,
            event_type=validation_event_type,
            aggregate_type="offer",
            aggregate_id=uuid4(),
            mission_id=mission_id,
            payload={},
            occurred_at=now,
        )
        session.add(event)
        session.flush()
        record_consumption_attempt(
            session,
            event=event,
            consumer_name=retry_consumer,
            outcome=ConsumptionOutcome.FAILED,
            failure_code="temporary_failure",
            attempted_at=now,
            next_retry_at=now + timedelta(minutes=1),
        )

    with sessions.begin() as session:
        started = perf_counter()
        assert not claim_unconsumed_events(
            session,
            consumer_name=retry_consumer,
            event_types=(validation_event_type,),
            now=now + timedelta(seconds=30),
        )
        assert perf_counter() - started < 1.0
    with sessions.begin() as session:
        claimed = claim_unconsumed_events(
            session,
            consumer_name=retry_consumer,
            event_types=(validation_event_type,),
            now=now + timedelta(minutes=2),
        )
        assert [item.id for item in claimed] == [event_id]
        record_consumption_attempt(
            session,
            event=claimed[0],
            consumer_name=retry_consumer,
            outcome=ConsumptionOutcome.DEAD_LETTERED,
            failure_code="attempts_exhausted",
            attempted_at=now + timedelta(minutes=2),
        )
    with sessions.begin() as session:
        assert not claim_unconsumed_events(
            session,
            consumer_name=retry_consumer,
            event_types=(validation_event_type,),
            now=now + timedelta(hours=1),
        )

    terminal_event_id = uuid4()
    with sessions.begin() as session:
        session.add(
            Event(
                id=terminal_event_id,
                event_type=validation_event_type,
                aggregate_type="offer",
                aggregate_id=uuid4(),
                mission_id=mission_id,
                payload={},
                occurred_at=now,
            )
        )
    terminal_barrier = Barrier(2)

    def insert_terminal(outcome: ConsumptionOutcome) -> bool:
        try:
            with sessions.begin() as session:
                event = session.get(Event, terminal_event_id)
                assert event is not None
                terminal_barrier.wait(timeout=10)
                record_consumption_attempt(
                    session,
                    event=event,
                    consumer_name=race_consumer,
                    outcome=outcome,
                    attempted_at=datetime.now(UTC),
                )
            return True
        except IntegrityError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        terminal_results = list(
            executor.map(
                insert_terminal,
                (ConsumptionOutcome.SUCCEEDED, ConsumptionOutcome.SKIPPED),
            )
        )
    assert sorted(terminal_results) == [False, True]

    terminal_cases: list[tuple[ConsumptionOutcome, str | None]] = [
        (ConsumptionOutcome.SUCCEEDED, None),
        (ConsumptionOutcome.SKIPPED, None),
        (ConsumptionOutcome.DEAD_LETTERED, "permanent_failure"),
    ]
    terminal_case_ids: list[UUID] = []
    with sessions.begin() as session:
        for index, (outcome, failure_code) in enumerate(terminal_cases):
            event = Event(
                id=uuid4(),
                event_type=validation_event_type,
                aggregate_type="offer",
                aggregate_id=uuid4(),
                mission_id=mission_id,
                payload={},
                occurred_at=now + timedelta(seconds=index),
            )
            session.add(event)
            session.flush()
            record_consumption_attempt(
                session,
                event=event,
                consumer_name=terminals_consumer,
                outcome=outcome,
                failure_code=failure_code,
                attempted_at=now + timedelta(seconds=index),
            )
            terminal_case_ids.append(event.id)
    with sessions.begin() as session:
        claimed_ids = {
            event.id
            for event in claim_unconsumed_events(
                session,
                consumer_name=terminals_consumer,
                event_types=(validation_event_type,),
                now=now + timedelta(days=1),
            )
        }
        assert not claimed_ids.intersection(terminal_case_ids)

    try:
        with sessions.begin() as session:
            session.execute(
                update(TelegramUpdateReceipt)
                .where(TelegramUpdateReceipt.update_id == update_id)
                .values(disposition=TelegramUpdateDisposition.DISCARDED.value)
            )
            session.flush()
    except DBAPIError:
        pass
    else:
        raise AssertionError("telegram_update_receipts accepted UPDATE")

    engine.dispose()
    print(
        "TASK-049 PostgreSQL validation passed: replay, rate limit, retry, "
        "dead letter, terminal concurrency and append-only receipts."
    )


if __name__ == "__main__":
    main()
