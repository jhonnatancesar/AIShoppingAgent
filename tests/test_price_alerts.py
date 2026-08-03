from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.alerts import PriceAlertEvaluationError, evaluate_price_alerts
from app.collection.models import PriceObservation
from app.collection.normalization import Availability
from app.events import EventType, PriceDecreasedPayload, PriceTargetReachedPayload
from app.missions.models import Mission, MissionCriteria, MissionStatus


def _mission(status: MissionStatus = MissionStatus.ACTIVE) -> Mission:
    mission = Mission(id=uuid4(), user_id=uuid4(), title="Notebook", status=status)
    return mission


def _criteria(
    mission: Mission,
    target: Decimal | None = Decimal("100.0000"),
    currency: str | None = "BRL",
) -> MissionCriteria:
    return MissionCriteria(
        id=uuid4(),
        mission_id=mission.id,
        search_query="notebook",
        target_amount=target,
        target_currency=currency,
    )


def _observation(
    offer_id,
    total: str,
    *,
    observed_at: datetime,
    currency: str = "BRL",
    availability: Availability = Availability.AVAILABLE,
) -> PriceObservation:
    amount = Decimal(total)
    return PriceObservation(
        id=uuid4(),
        offer_id=offer_id,
        collection_run_id=uuid4(),
        amount=amount,
        currency=currency,
        total_amount=amount,
        availability=availability,
        observed_at=observed_at,
    )


def test_price_drop_crossing_target_emits_two_catalog_events() -> None:
    mission = _mission()
    criteria = _criteria(mission)
    offer_id = uuid4()
    now = datetime.now(UTC)
    previous = _observation(offer_id, "120.0000", observed_at=now - timedelta(hours=1))
    current = _observation(offer_id, "90.0000", observed_at=now)

    alerts = evaluate_price_alerts(mission, criteria, current, previous)

    assert tuple(alert.event_type for alert in alerts) == (
        EventType.PRICE_DECREASED_V1,
        EventType.PRICE_TARGET_REACHED_V1,
    )
    assert isinstance(alerts[0].payload, PriceDecreasedPayload)
    assert isinstance(alerts[1].payload, PriceTargetReachedPayload)
    assert alerts[1].aggregate_id == mission.id


def test_target_alert_is_not_repeated_while_price_remains_below_target() -> None:
    mission = _mission()
    criteria = _criteria(mission)
    offer_id = uuid4()
    now = datetime.now(UTC)
    previous = _observation(offer_id, "95.0000", observed_at=now - timedelta(hours=1))
    current = _observation(offer_id, "95.0000", observed_at=now)

    assert evaluate_price_alerts(mission, criteria, current, previous) == ()


def test_first_available_observation_at_target_emits_target_alert() -> None:
    mission = _mission()
    criteria = _criteria(mission)
    current = _observation(uuid4(), "100.0000", observed_at=datetime.now(UTC))

    alerts = evaluate_price_alerts(mission, criteria, current)

    assert len(alerts) == 1
    assert alerts[0].event_type is EventType.PRICE_TARGET_REACHED_V1


def test_unavailable_previous_observation_is_not_a_price_baseline() -> None:
    mission = _mission()
    criteria = _criteria(mission)
    offer_id = uuid4()
    now = datetime.now(UTC)
    previous = _observation(
        offer_id,
        "80.0000",
        observed_at=now - timedelta(hours=1),
        availability=Availability.UNAVAILABLE,
    )
    current = _observation(offer_id, "90.0000", observed_at=now)

    alerts = evaluate_price_alerts(mission, criteria, current, previous)

    assert tuple(alert.event_type for alert in alerts) == (
        EventType.PRICE_TARGET_REACHED_V1,
    )


@pytest.mark.parametrize(
    ("status", "availability"),
    [
        (MissionStatus.PAUSED, Availability.AVAILABLE),
        (MissionStatus.ACTIVE, Availability.UNAVAILABLE),
        (MissionStatus.ACTIVE, Availability.UNKNOWN),
    ],
)
def test_inactive_mission_or_unavailable_offer_does_not_alert(
    status: MissionStatus, availability: Availability
) -> None:
    mission = _mission(status)
    criteria = _criteria(mission)
    current = _observation(
        uuid4(), "90.0000", observed_at=datetime.now(UTC), availability=availability
    )

    assert evaluate_price_alerts(mission, criteria, current) == ()


def test_different_currency_is_not_compared() -> None:
    mission = _mission()
    criteria = _criteria(mission)
    offer_id = uuid4()
    now = datetime.now(UTC)
    previous = _observation(offer_id, "120.0000", observed_at=now, currency="USD")
    current = _observation(offer_id, "90.0000", observed_at=now, currency="BRL")

    alerts = evaluate_price_alerts(mission, criteria, current, previous)

    assert tuple(alert.event_type for alert in alerts) == (
        EventType.PRICE_TARGET_REACHED_V1,
    )


def test_mission_without_target_can_still_emit_price_drop() -> None:
    mission = _mission()
    criteria = _criteria(mission, None, None)
    offer_id = uuid4()
    now = datetime.now(UTC)
    previous = _observation(offer_id, "120.0000", observed_at=now)
    current = _observation(offer_id, "110.0000", observed_at=now)

    alerts = evaluate_price_alerts(mission, criteria, current, previous)

    assert tuple(alert.event_type for alert in alerts) == (
        EventType.PRICE_DECREASED_V1,
    )


def test_evaluation_rejects_inconsistent_entities() -> None:
    mission = _mission()
    criteria = _criteria(mission)
    now = datetime.now(UTC)
    current = _observation(uuid4(), "90.0000", observed_at=now)
    other = _observation(uuid4(), "100.0000", observed_at=now)

    criteria.mission_id = uuid4()
    with pytest.raises(PriceAlertEvaluationError, match="criteria"):
        evaluate_price_alerts(mission, criteria, current)
    criteria.mission_id = mission.id
    with pytest.raises(PriceAlertEvaluationError, match="same offer"):
        evaluate_price_alerts(mission, criteria, current, other)


def test_evaluation_rejects_newer_previous_observation() -> None:
    mission = _mission()
    criteria = _criteria(mission)
    offer_id = uuid4()
    now = datetime.now(UTC)
    current = _observation(offer_id, "90.0000", observed_at=now)
    previous = _observation(
        offer_id, "100.0000", observed_at=now + timedelta(seconds=1)
    )

    with pytest.raises(PriceAlertEvaluationError, match="must not be newer"):
        evaluate_price_alerts(mission, criteria, current, previous)
