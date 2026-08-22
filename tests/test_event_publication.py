"""Testes da publicação durável de eventos (TASK-043)."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.collection.contracts import MarketplacePartyKind, OfferCondition
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.events import (
    AggregateType,
    AuthenticationSessionPayload,
    CollectionCompletedPayload,
    Event,
    EventCatalogError,
    EventPublicationError,
    EventType,
    MissionPrelistReadyV2Payload,
    MissionStatusChangedPayload,
    PrelistOfferPayload,
    PriceDecreasedPayload,
    PriceTargetReachedPayload,
    publish_event,
)
from app.missions.models import MissionStatus
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _session() -> MagicMock:
    return MagicMock()


def test_publish_event_persists_serialized_price_decrease_payload() -> None:
    offer_id, observation_id, previous_id = uuid4(), uuid4(), uuid4()
    payload = PriceDecreasedPayload(
        offer_id=offer_id,
        observation_id=observation_id,
        previous_observation_id=previous_id,
        previous_total=Decimal("120.0000"),
        current_total=Decimal("90.0000"),
        currency="BRL",
    )
    session = _session()

    event = publish_event(
        session,
        event_type=EventType.PRICE_DECREASED_V1,
        aggregate_type=AggregateType.OFFER,
        aggregate_id=offer_id,
        payload=payload,
        occurred_at=NOW,
    )

    assert event.event_type == "price.decreased.v1"
    assert event.aggregate_type == "offer"
    assert event.aggregate_id == offer_id
    assert event.mission_id is None
    assert event.occurred_at == NOW
    assert event.payload == {
        "offer_id": str(offer_id),
        "observation_id": str(observation_id),
        "previous_observation_id": str(previous_id),
        "previous_total": "120.0000",
        "current_total": "90.0000",
        "currency": "BRL",
    }
    session.add.assert_called_once_with(event)
    session.flush.assert_called_once_with()


def test_publish_event_serializes_enum_and_int_fields() -> None:
    mission_id, transition_id = uuid4(), uuid4()
    payload = MissionStatusChangedPayload(
        mission_id=mission_id,
        transition_id=transition_id,
        from_status=MissionStatus.DRAFT,
        to_status=MissionStatus.ACTIVE,
        state_version=1,
    )

    event = publish_event(
        _session(),
        event_type=EventType.MISSION_STATUS_CHANGED_V1,
        aggregate_type=AggregateType.MISSION,
        aggregate_id=mission_id,
        payload=payload,
        occurred_at=NOW,
        mission_id=mission_id,
    )

    assert event.mission_id == mission_id
    assert event.payload["from_status"] == "draft"
    assert event.payload["to_status"] == "active"
    assert event.payload["state_version"] == 1


def test_publish_event_serializes_optional_none_field() -> None:
    collection_run_id, store_id = uuid4(), uuid4()
    payload = CollectionCompletedPayload(
        collection_run_id=collection_run_id,
        store_id=store_id,
        mission_id=None,
        observation_count=3,
    )

    event = publish_event(
        _session(),
        event_type=EventType.COLLECTION_COMPLETED_V1,
        aggregate_type=AggregateType.COLLECTION_RUN,
        aggregate_id=collection_run_id,
        payload=payload,
        occurred_at=NOW,
    )

    assert event.payload["mission_id"] is None


def test_publish_event_serializes_nested_prelist_v2_collection() -> None:
    mission_id = uuid4()
    item = PrelistOfferPayload(
        uuid4(),
        uuid4(),
        uuid4(),
        Decimal("100.00"),
        Decimal("100.00"),
        "BRL",
        OfferRelevance.MATCH,
        OfferCondition.NEW,
        MarketplacePartyKind.PLATFORM,
        Availability.AVAILABLE,
    )

    event = publish_event(
        _session(),
        event_type=EventType.MISSION_PRELIST_READY_V2,
        aggregate_type=AggregateType.MISSION,
        aggregate_id=mission_id,
        payload=MissionPrelistReadyV2Payload(mission_id, (item,)),
        occurred_at=NOW,
        mission_id=mission_id,
    )

    assert event.payload["offers"] == [
        {
            "offer_id": str(item.offer_id),
            "observation_id": str(item.observation_id),
            "store_id": str(item.store_id),
            "amount": "100.00",
            "total_amount": "100.00",
            "currency": "BRL",
            "relevance": "match",
            "condition": "new",
            "seller_kind": "platform",
            "availability": "available",
        }
    ]


def test_publish_event_serializes_aware_payload_datetime() -> None:
    session_id = uuid4()
    expires_at = datetime(2026, 8, 9, 22, 30, tzinfo=UTC)
    event = publish_event(
        _session(),
        event_type=EventType.AUTHENTICATION_SESSION_EXPIRED_V1,
        aggregate_type=AggregateType.AUTH_SESSION,
        aggregate_id=session_id,
        payload=AuthenticationSessionPayload(session_id, uuid4(), expires_at),
        occurred_at=NOW,
    )

    assert event.payload["expires_at"] == expires_at.isoformat()


def test_publish_event_rejects_naive_occurred_at() -> None:
    payload = PriceDecreasedPayload(
        offer_id=uuid4(),
        observation_id=uuid4(),
        previous_observation_id=uuid4(),
        previous_total=Decimal("100"),
        current_total=Decimal("90"),
        currency="BRL",
    )

    with pytest.raises(EventPublicationError, match="timezone-aware"):
        publish_event(
            _session(),
            event_type=EventType.PRICE_DECREASED_V1,
            aggregate_type=AggregateType.OFFER,
            aggregate_id=payload.offer_id,
            payload=payload,
            occurred_at=datetime(2026, 8, 8, 12, 0),
        )


def test_publish_event_rejects_aggregate_type_mismatch() -> None:
    offer_id = uuid4()
    payload = PriceDecreasedPayload(
        offer_id=offer_id,
        observation_id=uuid4(),
        previous_observation_id=uuid4(),
        previous_total=Decimal("100"),
        current_total=Decimal("90"),
        currency="BRL",
    )
    session = _session()

    with pytest.raises(EventPublicationError, match="aggregate_type"):
        publish_event(
            session,
            event_type=EventType.PRICE_DECREASED_V1,
            aggregate_type=AggregateType.MISSION,
            aggregate_id=offer_id,
            payload=payload,
            occurred_at=NOW,
        )
    session.add.assert_not_called()
    session.flush.assert_not_called()


@pytest.mark.parametrize(
    ("aggregate_type", "payload"),
    [
        (
            AggregateType.MISSION,
            MissionStatusChangedPayload(
                mission_id=uuid4(),
                transition_id=uuid4(),
                from_status=MissionStatus.DRAFT,
                to_status=MissionStatus.ACTIVE,
                state_version=1,
            ),
        ),
        (
            AggregateType.COLLECTION_RUN,
            CollectionCompletedPayload(
                collection_run_id=uuid4(),
                store_id=uuid4(),
                mission_id=None,
                observation_count=1,
            ),
        ),
        (
            AggregateType.OFFER,
            PriceDecreasedPayload(
                offer_id=uuid4(),
                observation_id=uuid4(),
                previous_observation_id=uuid4(),
                previous_total=Decimal("100"),
                current_total=Decimal("90"),
                currency="BRL",
            ),
        ),
    ],
)
def test_publish_event_rejects_aggregate_id_mismatching_payload(
    aggregate_type: AggregateType,
    payload: (
        MissionStatusChangedPayload | CollectionCompletedPayload | PriceDecreasedPayload
    ),
) -> None:
    session = _session()

    with pytest.raises(EventPublicationError, match="aggregate_id"):
        publish_event(
            session,
            event_type={
                AggregateType.MISSION: EventType.MISSION_STATUS_CHANGED_V1,
                AggregateType.COLLECTION_RUN: EventType.COLLECTION_COMPLETED_V1,
                AggregateType.OFFER: EventType.PRICE_DECREASED_V1,
            }[aggregate_type],
            aggregate_type=aggregate_type,
            aggregate_id=uuid4(),
            payload=payload,
            occurred_at=NOW,
        )

    session.add.assert_not_called()
    session.flush.assert_not_called()


def test_publish_event_propagates_catalog_payload_mismatch() -> None:
    mismatched_payload = PriceTargetReachedPayload(
        mission_id=uuid4(),
        offer_id=uuid4(),
        observation_id=uuid4(),
        target_total=Decimal("100"),
        current_total=Decimal("90"),
        currency="BRL",
    )
    session = _session()

    with pytest.raises(EventCatalogError, match="requires PriceDecreasedPayload"):
        publish_event(
            session,
            event_type=EventType.PRICE_DECREASED_V1,
            aggregate_type=AggregateType.OFFER,
            aggregate_id=uuid4(),
            payload=mismatched_payload,
            occurred_at=NOW,
        )
    session.add.assert_not_called()


def test_events_table_matches_durable_publication_contract() -> None:
    table = Event.__table__
    assert [column.name for column in table.columns] == [
        "id",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "mission_id",
        "payload",
        "occurred_at",
        "recorded_at",
    ]
    assert "updated_at" not in table.c
    assert table.c.mission_id.nullable is True
    assert table.c.payload.nullable is False
    assert {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    } == {
        "ck_events_event_type_not_blank",
        "ck_events_aggregate_type_not_blank",
        "ck_events_payload_object",
    }
    foreign_keys = {
        tuple(constraint.columns)[0].name: tuple(constraint.elements)[0]
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys["mission_id"].target_fullname == "missions.id"
    assert foreign_keys["mission_id"].ondelete == "RESTRICT"
    index_names = {index.name for index in table.indexes if isinstance(index, Index)}
    assert index_names == {"ix_events_aggregate_history", "ix_events_mission_history"}


def test_event_model_is_registered_in_shared_metadata() -> None:
    assert Event in REGISTERED_MODELS
    assert Base.metadata.tables["events"] is Event.__table__
