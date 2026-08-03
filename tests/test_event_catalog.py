from decimal import Decimal
from types import MappingProxyType
from uuid import uuid4

import pytest
from app.collection.normalization import Availability
from app.events import (
    EVENT_CATALOG,
    AggregateType,
    AvailabilityChangedPayload,
    CollectionCompletedPayload,
    CollectionFailedPayload,
    EventCatalogError,
    EventType,
    MissionStatusChangedPayload,
    PriceDecreasedPayload,
    PriceTargetReachedPayload,
    resolve_event_spec,
    validate_event_payload,
)
from app.missions.models import MissionStatus


def test_catalog_is_closed_versioned_and_has_expected_aggregates() -> None:
    assert isinstance(EVENT_CATALOG, MappingProxyType)
    assert set(EVENT_CATALOG) == set(EventType)
    assert all(event_type.value.endswith(".v1") for event_type in EventType)
    assert (
        resolve_event_spec("price.target_reached.v1").aggregate_type
        is AggregateType.MISSION
    )
    assert (
        resolve_event_spec(EventType.PRICE_DECREASED_V1).aggregate_type
        is AggregateType.OFFER
    )


def test_catalog_accepts_exact_payload_contract() -> None:
    payload = CollectionCompletedPayload(uuid4(), uuid4(), uuid4(), 3)

    spec = validate_event_payload(EventType.COLLECTION_COMPLETED_V1, payload)

    assert spec.payload_type is CollectionCompletedPayload


def test_catalog_rejects_unknown_event_and_wrong_payload() -> None:
    payload = CollectionCompletedPayload(uuid4(), uuid4(), None, 0)
    with pytest.raises(EventCatalogError, match="unknown event type"):
        resolve_event_spec("price.unversioned")
    with pytest.raises(EventCatalogError, match="requires PriceDecreasedPayload"):
        validate_event_payload(EventType.PRICE_DECREASED_V1, payload)


def test_mission_transition_contract_requires_a_real_transition() -> None:
    with pytest.raises(EventCatalogError, match="status must change"):
        MissionStatusChangedPayload(
            uuid4(), uuid4(), MissionStatus.ACTIVE, MissionStatus.ACTIVE, 1
        )
    with pytest.raises(EventCatalogError, match="state_version"):
        MissionStatusChangedPayload(
            uuid4(), uuid4(), MissionStatus.DRAFT, MissionStatus.ACTIVE, 0
        )


def test_collection_contract_rejects_negative_count() -> None:
    with pytest.raises(EventCatalogError, match="observation_count"):
        CollectionCompletedPayload(uuid4(), uuid4(), None, -1)


def test_collection_failure_requires_stable_sanitized_code() -> None:
    with pytest.raises(EventCatalogError, match="snake_case"):
        CollectionFailedPayload(uuid4(), uuid4(), None, "Timeout contacting https://x")


def test_price_decrease_contract_uses_exact_decreasing_money() -> None:
    payload = PriceDecreasedPayload(
        uuid4(), uuid4(), uuid4(), Decimal("100.0000"), Decimal("90.0000"), "BRL"
    )
    assert payload.current_total == Decimal("90.0000")

    with pytest.raises(EventCatalogError, match="lower"):
        PriceDecreasedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100.0000"),
            Decimal("100.0000"),
            "BRL",
        )
    with pytest.raises(EventCatalogError, match="Decimal"):
        PriceDecreasedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100"),
            90.0,
            "BRL",  # type: ignore[arg-type]
        )


def test_target_and_availability_contracts_enforce_their_facts() -> None:
    with pytest.raises(EventCatalogError, match="target_total"):
        PriceTargetReachedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Decimal("100.0000"),
            Decimal("100.0001"),
            "BRL",
        )
    with pytest.raises(EventCatalogError, match="availability must change"):
        AvailabilityChangedPayload(
            uuid4(),
            uuid4(),
            uuid4(),
            Availability.AVAILABLE,
            Availability.AVAILABLE,
        )


@pytest.mark.parametrize("currency", ["brl", "BR", "BR1", "ÉUR"])
def test_money_contract_rejects_invalid_currency(currency: str) -> None:
    with pytest.raises(EventCatalogError, match="currency"):
        PriceTargetReachedPayload(
            uuid4(), uuid4(), uuid4(), Decimal("100"), Decimal("90"), currency
        )
