"""Contratos estáveis dos eventos de domínio do MVP."""

from app.events.catalog import (
    EVENT_CATALOG,
    AggregateType,
    AvailabilityChangedPayload,
    CollectionCompletedPayload,
    CollectionFailedPayload,
    EventCatalogError,
    EventSpec,
    EventType,
    MissionStatusChangedPayload,
    PriceDecreasedPayload,
    PriceTargetReachedPayload,
    resolve_event_spec,
    validate_event_payload,
)
from app.events.consumption import (
    EventConsumptionError,
    claim_unconsumed_events,
    record_consumption_attempt,
)
from app.events.models import ConsumptionOutcome, Event, EventConsumptionAttempt
from app.events.service import EventPublicationError, publish_event

__all__ = [
    "EVENT_CATALOG",
    "AggregateType",
    "AvailabilityChangedPayload",
    "CollectionCompletedPayload",
    "CollectionFailedPayload",
    "ConsumptionOutcome",
    "Event",
    "EventCatalogError",
    "EventConsumptionAttempt",
    "EventConsumptionError",
    "EventPublicationError",
    "EventSpec",
    "EventType",
    "MissionStatusChangedPayload",
    "PriceDecreasedPayload",
    "PriceTargetReachedPayload",
    "claim_unconsumed_events",
    "publish_event",
    "record_consumption_attempt",
    "resolve_event_spec",
    "validate_event_payload",
]
