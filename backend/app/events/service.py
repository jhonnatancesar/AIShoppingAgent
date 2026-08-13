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

from sqlalchemy.ext.asyncio import AsyncSession
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


_AGGREGATE_ID_FIELDS = {
    AggregateType.MISSION: "mission_id",
    AggregateType.COLLECTION_RUN: "collection_run_id",
    AggregateType.OFFER: "offer_id",
    AggregateType.USER: "user_id",
    AggregateType.AUTH_SESSION: "session_id",
}


def _build_event(
    *,
    event_type: EventType,
    aggregate_type: AggregateType,
    aggregate_id: UUID,
    payload: EventPayload,
    occurred_at: datetime,
    mission_id: UUID | None,
) -> Event:
    """Valida o evento e devolve a instância pronta para `add`, sem I/O."""
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise EventPublicationError("occurred_at must be timezone-aware")

    spec = validate_event_payload(event_type, payload)
    if spec.aggregate_type is not aggregate_type:
        raise EventPublicationError(
            f"{event_type} requires aggregate_type {spec.aggregate_type}"
        )
    payload_aggregate_id = getattr(payload, _AGGREGATE_ID_FIELDS[spec.aggregate_type])
    if aggregate_id != payload_aggregate_id:
        raise EventPublicationError(
            "aggregate_id must match the aggregate identifier in payload"
        )

    return Event(
        event_type=event_type.value,
        aggregate_type=aggregate_type.value,
        aggregate_id=aggregate_id,
        mission_id=mission_id,
        payload=_serialize_payload(payload),
        occurred_at=occurred_at,
    )


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
    event = _build_event(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        occurred_at=occurred_at,
        mission_id=mission_id,
    )
    session.add(event)
    session.flush()
    return event


async def publish_event_async(
    session: AsyncSession,
    *,
    event_type: EventType,
    aggregate_type: AggregateType,
    aggregate_id: UUID,
    payload: EventPayload,
    occurred_at: datetime,
    mission_id: UUID | None = None,
) -> Event:
    """Equivalente assíncrono de `publish_event` (TASK-079).

    Usado só pelo caminho async do `collection_worker` -- API, Telegram e
    demais chamadores continuam usando `publish_event` (síncrono). Mesma
    validação, via `_build_event`, para nunca divergir do comportamento
    da versão síncrona.
    """
    event = _build_event(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=payload,
        occurred_at=occurred_at,
        mission_id=mission_id,
    )
    session.add(event)
    await session.flush()
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
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise EventPublicationError("payload datetime must be timezone-aware")
        return value.isoformat()
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise EventPublicationError(f"unsupported payload value type: {type(value)!r}")
