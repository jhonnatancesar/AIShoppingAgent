"""Catálogo fechado e versionado de eventos de domínio da V1."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from re import fullmatch
from types import MappingProxyType
from uuid import UUID

from app.authentication.models import CredentialAction
from app.collection.normalization import Availability
from app.missions.models import MissionStatus


class EventCatalogError(ValueError):
    """Indica evento desconhecido ou payload incompatível com o catálogo."""


class AggregateType(StrEnum):
    MISSION = "mission"
    COLLECTION_RUN = "collection_run"
    OFFER = "offer"
    USER = "user"
    AUTH_SESSION = "auth_session"


class EventType(StrEnum):
    MISSION_STATUS_CHANGED_V1 = "mission.status_changed.v1"
    COLLECTION_COMPLETED_V1 = "collection.completed.v1"
    COLLECTION_FAILED_V1 = "collection.failed.v1"
    PRICE_DECREASED_V1 = "price.decreased.v1"
    PRICE_TARGET_REACHED_V1 = "price.target_reached.v1"
    AVAILABILITY_CHANGED_V1 = "offer.availability_changed.v1"
    AUTHENTICATION_COMPLETED_V1 = "authentication.completed.v1"
    AUTHENTICATION_SESSION_EXPIRING_V1 = "authentication.session_expiring.v1"
    AUTHENTICATION_SESSION_EXPIRED_V1 = "authentication.session_expired.v1"
    MISSION_PRELIST_READY_V1 = "mission.prelist_ready.v1"
    MISSION_PRELIST_ERRATA_V1 = "mission.prelist_errata.v1"


@dataclass(frozen=True, slots=True)
class AuthenticationCompletedPayload:
    user_id: UUID
    action: CredentialAction

    def __post_init__(self) -> None:
        if not isinstance(self.action, CredentialAction):
            raise EventCatalogError("action must use CredentialAction")


@dataclass(frozen=True, slots=True)
class AuthenticationSessionPayload:
    session_id: UUID
    user_id: UUID
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise EventCatalogError("expires_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MissionStatusChangedPayload:
    mission_id: UUID
    transition_id: UUID
    from_status: MissionStatus
    to_status: MissionStatus
    state_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.from_status, MissionStatus) or not isinstance(
            self.to_status, MissionStatus
        ):
            raise EventCatalogError("mission statuses must use MissionStatus")
        if self.from_status is self.to_status:
            raise EventCatalogError("mission status must change")
        if type(self.state_version) is not int or self.state_version < 1:
            raise EventCatalogError("state_version must be positive")


@dataclass(frozen=True, slots=True)
class CollectionCompletedPayload:
    collection_run_id: UUID
    store_id: UUID
    mission_id: UUID | None
    observation_count: int

    def __post_init__(self) -> None:
        if type(self.observation_count) is not int or self.observation_count < 0:
            raise EventCatalogError("observation_count must be non-negative")


@dataclass(frozen=True, slots=True)
class CollectionFailedPayload:
    collection_run_id: UUID
    store_id: UUID
    mission_id: UUID | None
    failure_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.failure_code, str) or not fullmatch(
            r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", self.failure_code
        ):
            raise EventCatalogError("failure_code must use stable snake_case")


@dataclass(frozen=True, slots=True)
class PriceDecreasedPayload:
    offer_id: UUID
    observation_id: UUID
    previous_observation_id: UUID
    previous_total: Decimal
    current_total: Decimal
    currency: str

    def __post_init__(self) -> None:
        _validate_money(self.previous_total, self.currency)
        _validate_money(self.current_total, self.currency)
        if self.current_total >= self.previous_total:
            raise EventCatalogError("current_total must be lower than previous_total")


@dataclass(frozen=True, slots=True)
class PriceTargetReachedPayload:
    mission_id: UUID
    offer_id: UUID
    observation_id: UUID
    target_total: Decimal
    current_total: Decimal
    currency: str

    def __post_init__(self) -> None:
        _validate_money(self.target_total, self.currency)
        _validate_money(self.current_total, self.currency)
        if self.current_total > self.target_total:
            raise EventCatalogError("current_total must not exceed target_total")


@dataclass(frozen=True, slots=True)
class AvailabilityChangedPayload:
    offer_id: UUID
    observation_id: UUID
    previous_observation_id: UUID
    previous_availability: Availability
    current_availability: Availability

    def __post_init__(self) -> None:
        if not isinstance(self.previous_availability, Availability) or not isinstance(
            self.current_availability, Availability
        ):
            raise EventCatalogError("availability values must use Availability")
        if self.previous_availability is self.current_availability:
            raise EventCatalogError("availability must change")


@dataclass(frozen=True, slots=True)
class MissionPrelistReadyPayload:
    """TASK-068: até 2 ofertas `MATCH` mais baratas encontradas na primeira
    rodada de coleta da missão, sem nenhum julgamento de IA sobre elas."""

    mission_id: UUID
    first_offer_id: UUID
    first_observation_id: UUID
    first_total: Decimal
    first_currency: str
    second_offer_id: UUID | None = None
    second_observation_id: UUID | None = None
    second_total: Decimal | None = None
    second_currency: str | None = None

    def __post_init__(self) -> None:
        _validate_money(self.first_total, self.first_currency)
        has_second = self.second_offer_id is not None
        second_complete = (
            self.second_observation_id is not None
            and self.second_total is not None
            and self.second_currency is not None
        )
        if has_second != second_complete:
            raise EventCatalogError(
                "second offer fields must be complete or all absent"
            )
        if has_second:
            _validate_money(self.second_total, self.second_currency)
            if self.second_total < self.first_total:
                raise EventCatalogError("first_total must be the lowest of the two")
            if self.second_offer_id == self.first_offer_id:
                raise EventCatalogError("first and second offers must differ")


@dataclass(frozen=True, slots=True)
class MissionPrelistErrataPayload:
    """TASK-068: única correção permitida quando uma coleta posterior à
    pré-lista encontra uma oferta `MATCH` mais barata que a base já
    enviada -- `previous_lowest_total` é `None` só quando a pré-lista
    original não teve nenhuma oferta para mostrar."""

    mission_id: UUID
    offer_id: UUID
    observation_id: UUID
    current_total: Decimal
    currency: str
    previous_lowest_total: Decimal | None

    def __post_init__(self) -> None:
        _validate_money(self.current_total, self.currency)
        if self.previous_lowest_total is not None:
            _validate_money(self.previous_lowest_total, self.currency)
            if self.current_total >= self.previous_lowest_total:
                raise EventCatalogError(
                    "current_total must be lower than previous_lowest_total"
                )


type EventPayload = (
    MissionStatusChangedPayload
    | CollectionCompletedPayload
    | CollectionFailedPayload
    | PriceDecreasedPayload
    | PriceTargetReachedPayload
    | AvailabilityChangedPayload
    | AuthenticationCompletedPayload
    | AuthenticationSessionPayload
    | MissionPrelistReadyPayload
    | MissionPrelistErrataPayload
)


@dataclass(frozen=True, slots=True)
class EventSpec:
    event_type: EventType
    aggregate_type: AggregateType
    payload_type: type[EventPayload]


EVENT_CATALOG = MappingProxyType(
    {
        EventType.MISSION_STATUS_CHANGED_V1: EventSpec(
            EventType.MISSION_STATUS_CHANGED_V1,
            AggregateType.MISSION,
            MissionStatusChangedPayload,
        ),
        EventType.COLLECTION_COMPLETED_V1: EventSpec(
            EventType.COLLECTION_COMPLETED_V1,
            AggregateType.COLLECTION_RUN,
            CollectionCompletedPayload,
        ),
        EventType.COLLECTION_FAILED_V1: EventSpec(
            EventType.COLLECTION_FAILED_V1,
            AggregateType.COLLECTION_RUN,
            CollectionFailedPayload,
        ),
        EventType.PRICE_DECREASED_V1: EventSpec(
            EventType.PRICE_DECREASED_V1,
            AggregateType.OFFER,
            PriceDecreasedPayload,
        ),
        EventType.PRICE_TARGET_REACHED_V1: EventSpec(
            EventType.PRICE_TARGET_REACHED_V1,
            AggregateType.MISSION,
            PriceTargetReachedPayload,
        ),
        EventType.AVAILABILITY_CHANGED_V1: EventSpec(
            EventType.AVAILABILITY_CHANGED_V1,
            AggregateType.OFFER,
            AvailabilityChangedPayload,
        ),
        EventType.AUTHENTICATION_COMPLETED_V1: EventSpec(
            EventType.AUTHENTICATION_COMPLETED_V1,
            AggregateType.USER,
            AuthenticationCompletedPayload,
        ),
        EventType.AUTHENTICATION_SESSION_EXPIRING_V1: EventSpec(
            EventType.AUTHENTICATION_SESSION_EXPIRING_V1,
            AggregateType.AUTH_SESSION,
            AuthenticationSessionPayload,
        ),
        EventType.AUTHENTICATION_SESSION_EXPIRED_V1: EventSpec(
            EventType.AUTHENTICATION_SESSION_EXPIRED_V1,
            AggregateType.AUTH_SESSION,
            AuthenticationSessionPayload,
        ),
        EventType.MISSION_PRELIST_READY_V1: EventSpec(
            EventType.MISSION_PRELIST_READY_V1,
            AggregateType.MISSION,
            MissionPrelistReadyPayload,
        ),
        EventType.MISSION_PRELIST_ERRATA_V1: EventSpec(
            EventType.MISSION_PRELIST_ERRATA_V1,
            AggregateType.MISSION,
            MissionPrelistErrataPayload,
        ),
    }
)


def resolve_event_spec(event_type: EventType | str) -> EventSpec:
    try:
        normalized = EventType(event_type)
    except ValueError as error:
        raise EventCatalogError(f"unknown event type: {event_type}") from error
    return EVENT_CATALOG[normalized]


def validate_event_payload(
    event_type: EventType | str, payload: EventPayload
) -> EventSpec:
    spec = resolve_event_spec(event_type)
    if type(payload) is not spec.payload_type:
        raise EventCatalogError(
            f"{spec.event_type} requires {spec.payload_type.__name__}"
        )
    return spec


def _validate_money(amount: Decimal, currency: str) -> None:
    if not isinstance(amount, Decimal):
        raise EventCatalogError("monetary values must use Decimal")
    if amount < 0:
        raise EventCatalogError("monetary values must be non-negative")
    if not isinstance(currency, str) or (
        len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
        or not currency.isupper()
    ):
        raise EventCatalogError("currency must use three uppercase ASCII letters")
