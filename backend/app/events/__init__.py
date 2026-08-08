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
from app.events.models import Event
from app.events.service import EventPublicationError, publish_event

__all__ = [
    "EVENT_CATALOG",
    "AggregateType",
    "AvailabilityChangedPayload",
    "CollectionCompletedPayload",
    "CollectionFailedPayload",
    "Event",
    "EventCatalogError",
    "EventPublicationError",
    "EventSpec",
    "EventType",
    "MissionStatusChangedPayload",
    "PriceDecreasedPayload",
    "PriceTargetReachedPayload",
    "publish_event",
    "resolve_event_spec",
    "validate_event_payload",
]
