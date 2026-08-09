"""Fluxo persistente missão → preço → alerta → evento → consumo."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from app.alerts.evaluator import evaluate_price_alerts
from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    PriceObservation,
)
from app.collection.normalization import Availability
from app.events import (
    ConsumptionOutcome,
    Event,
    EventType,
    claim_unconsumed_events,
    publish_event,
    record_consumption_attempt,
)
from app.missions.models import Mission, MissionCriteria, MissionSource, MissionStatus
from app.offers.models import Offer
from app.products.models import Product
from app.stores.models import Store
from app.users.models import User, UserRole
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def test_price_alert_publication_and_concurrent_consumption(
    integration_database,
) -> None:
    now = datetime.now(UTC)
    consumer = f"task052_{uuid4().hex[:12]}"
    with integration_database.sessions.begin() as session:
        store = session.scalar(select(Store).where(Store.code == "pichau"))
        assert store is not None
        user = User(display_name="TASK-052 synthetic", role=UserRole.USER)
        product = Product(name="TASK-052 synthetic product")
        session.add_all((user, product))
        session.flush()
        mission = Mission(
            user_id=user.id,
            title="TASK-052 synthetic mission",
            status=MissionStatus.ACTIVE,
        )
        offer = Offer(
            product_id=product.id,
            store_id=store.id,
            external_id=f"task052-{uuid4().hex}",
            url=f"https://example.invalid/{uuid4().hex}",
        )
        session.add_all((mission, offer))
        session.flush()
        criteria = MissionCriteria(
            mission_id=mission.id,
            search_query="synthetic product",
            target_amount=Decimal("2500"),
            target_currency="BRL",
        )
        run = CollectionRun(
            mission_id=mission.id,
            store_id=store.id,
            status=CollectionRunStatus.SUCCEEDED,
            started_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
        )
        session.add_all(
            (criteria, MissionSource(mission_id=mission.id, store_id=store.id), run)
        )
        session.flush()
        previous = PriceObservation(
            offer_id=offer.id,
            collection_run_id=run.id,
            amount=Decimal("2900"),
            shipping_amount=Decimal("100"),
            total_amount=Decimal("3000"),
            currency="BRL",
            availability=Availability.AVAILABLE,
            observed_at=now - timedelta(minutes=1),
            raw_evidence={"source": "task052_synthetic"},
        )
        current = PriceObservation(
            offer_id=offer.id,
            collection_run_id=run.id,
            amount=Decimal("2300"),
            shipping_amount=Decimal("100"),
            total_amount=Decimal("2400"),
            currency="BRL",
            availability=Availability.AVAILABLE,
            observed_at=now,
            raw_evidence={"source": "task052_synthetic"},
        )
        session.add_all((previous, current))
        session.flush()
        candidates = evaluate_price_alerts(mission, criteria, current, previous)
        assert {candidate.event_type for candidate in candidates} == {
            EventType.PRICE_DECREASED_V1,
            EventType.PRICE_TARGET_REACHED_V1,
        }
        event_ids = []
        for candidate in candidates:
            event = publish_event(
                session,
                event_type=candidate.event_type,
                aggregate_type=candidate.aggregate_type,
                aggregate_id=candidate.aggregate_id,
                payload=candidate.payload,
                occurred_at=now,
                mission_id=mission.id,
            )
            event_ids.append(event.id)

    barrier = Barrier(2)

    def consume() -> tuple[int, tuple]:
        with integration_database.sessions.begin() as session:
            backend_pid = session.scalar(select(text("pg_backend_pid()")))
            barrier.wait(timeout=10)
            claimed = claim_unconsumed_events(
                session,
                consumer_name=consumer,
                event_types={candidate.value for candidate in EventType},
                limit=10,
            )
            for event in claimed:
                record_consumption_attempt(
                    session,
                    event=event,
                    consumer_name=consumer,
                    outcome=ConsumptionOutcome.SUCCEEDED,
                    attempted_at=datetime.now(UTC),
                )
            return backend_pid, tuple(event.id for event in claimed)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: consume(), range(2)))
    assert results[0][0] != results[1][0]
    claimed_sets = [set(result[1]) for result in results]
    assert claimed_sets[0].isdisjoint(claimed_sets[1])
    assert claimed_sets[0] | claimed_sets[1] == set(event_ids)

    with integration_database.sessions.begin() as session:
        assert not claim_unconsumed_events(
            session,
            consumer_name=consumer,
            event_types={candidate.value for candidate in EventType},
        )
        event = session.get(Event, event_ids[0])
        assert event is not None
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                session.execute(
                    update(Event).where(Event.id == event.id).values(payload={})
                )
