"""Publicação durável de eventos de domínio validados pelo catálogo.

"Publicar" nesta TASK significa exclusivamente validar e registrar o
evento de forma durável e append-only na tabela `events` — a própria
tabela é o log de publicação. Não inclui message broker, consumidor,
notificação nem qualquer parte da TASK-044.
"""

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.events.catalog import (
    AggregateType,
    EventPayload,
    EventType,
    validate_event_payload,
)
from app.events.models import Event


class EventPublicationError(ValueError):
    """Indica evento inválido para publicação."""


def publish_event(
    session: Session,
    *,
    event_type: EventType,
    aggregate_type: AggregateType,
    aggregate_id: UUID,
    payload: EventPayload,
    occurred_at: datetime,
    mission_id: UUID | None = None,
) -> Event:
    """Valida e persiste um evento de forma durável e append-only."""
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise EventPublicationError("occurred_at must be timezone-aware")

    spec = validate_event_payload(event_type, payload)
    if spec.aggregate_type is not aggregate_type:
        raise EventPublicationError(
            f"{event_type} requires aggregate_type {spec.aggregate_type}"
        )

    event = Event(
        event_type=event_type.value,
        aggregate_type=aggregate_type.value,
        aggregate_id=aggregate_id,
        mission_id=mission_id,
        payload=_serialize_payload(payload),
        occurred_at=occurred_at,
    )
    session.add(event)
    session.flush()
    return event


def _serialize_payload(payload: EventPayload) -> dict[str, Any]:
    """Converte um payload do catálogo em JSON seguro, sem perda de precisão."""
    if not is_dataclass(payload):
        raise EventPublicationError("payload must be a catalog dataclass")
    return {
        field.name: _serialize_value(getattr(payload, field.name))
        for field in fields(payload)
    }


def _serialize_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise EventPublicationError(f"unsupported payload value type: {type(value)!r}")
