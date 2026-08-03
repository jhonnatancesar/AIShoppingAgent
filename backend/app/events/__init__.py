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

__all__ = [
    "EVENT_CATALOG",
    "AggregateType",
    "AvailabilityChangedPayload",
    "CollectionCompletedPayload",
    "CollectionFailedPayload",
    "EventCatalogError",
    "EventSpec",
    "EventType",
    "MissionStatusChangedPayload",
    "PriceDecreasedPayload",
    "PriceTargetReachedPayload",
    "resolve_event_spec",
    "validate_event_payload",
]
