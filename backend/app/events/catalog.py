"""Catálogo fechado e versionado de eventos de domínio da V1."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from re import fullmatch
from types import MappingProxyType
from uuid import UUID

from app.authentication.models import CredentialAction
from app.collection.contracts import MarketplacePartyKind, OfferCondition
from app.collection.normalization import Availability
from app.collection.relevance import OfferRelevance
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
    MISSION_PRELIST_READY_V2 = "mission.prelist_ready.v2"
    MISSION_PRELIST_ERRATA_V2 = "mission.prelist_errata.v2"
    MISSION_VARIANTS_READY_V1 = "mission.variants_ready.v1"


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
class AppliedCouponPayload:
    """Snapshot IMUTÁVEL do cupom que efetivamente produziu `current_
    total` desta decisão (consumo de cupons, correção 2026-09-06) --
    preservado no próprio evento porque a linha `Coupon` pode ser
    atualizada, expirar, ou um cupom melhor pode aparecer depois. O
    alerta precisa reproduzir a MESMA oportunidade aprovada por
    F2/F3/pelo evaluator, nunca uma nova busca no momento do envio --
    ver `app.coupons.pricing.AppliedCoupon`, que espelha estes mesmos
    campos no lado do consumo."""

    coupon_id: UUID
    code: str
    discount_kind: str
    original_amount: Decimal
    discount_amount: Decimal
    final_amount: Decimal
    currency: str
    raw_rule_text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise EventCatalogError("coupon code must be a string")
        _validate_money(self.original_amount, self.currency)
        _validate_money(self.discount_amount, self.currency)
        _validate_money(self.final_amount, self.currency)
        if self.discount_amount > self.original_amount:
            raise EventCatalogError(
                "coupon discount_amount must not exceed original_amount"
            )
        if self.final_amount != self.original_amount - self.discount_amount:
            raise EventCatalogError(
                "coupon final_amount must equal original_amount minus discount_amount"
            )


def _validate_coupon_snapshot(
    coupon: AppliedCouponPayload | None, *, current_total: Decimal, currency: str
) -> None:
    """O snapshot e `current_total` precisam representar a MESMA
    oportunidade -- nunca um cupom cujo preço final diverge do que o
    alerta está de fato anunciando (correção 2026-09-06)."""
    if coupon is None:
        return
    if not isinstance(coupon, AppliedCouponPayload):
        raise EventCatalogError("coupon must use AppliedCouponPayload")
    if coupon.currency != currency:
        raise EventCatalogError("coupon currency must match the alert currency")
    if coupon.final_amount != current_total:
        raise EventCatalogError(
            "coupon final_amount must equal current_total -- same opportunity"
        )


@dataclass(frozen=True, slots=True)
class PriceDecreasedPayload:
    offer_id: UUID
    observation_id: UUID
    previous_observation_id: UUID
    previous_total: Decimal
    current_total: Decimal
    currency: str
    coupon: AppliedCouponPayload | None = None

    def __post_init__(self) -> None:
        _validate_money(self.previous_total, self.currency)
        _validate_money(self.current_total, self.currency)
        if self.current_total >= self.previous_total:
            raise EventCatalogError("current_total must be lower than previous_total")
        _validate_coupon_snapshot(
            self.coupon, current_total=self.current_total, currency=self.currency
        )


@dataclass(frozen=True, slots=True)
class PriceTargetReachedPayload:
    mission_id: UUID
    offer_id: UUID
    observation_id: UUID
    target_total: Decimal
    current_total: Decimal
    currency: str
    coupon: AppliedCouponPayload | None = None

    def __post_init__(self) -> None:
        _validate_money(self.target_total, self.currency)
        _validate_money(self.current_total, self.currency)
        if self.current_total > self.target_total:
            raise EventCatalogError("current_total must not exceed target_total")
        _validate_coupon_snapshot(
            self.coupon, current_total=self.current_total, currency=self.currency
        )


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
    rodada de coleta da missão, sem nenhum julgamento de IA sobre elas.

    Ranqueia por `PriceObservation.amount` (preço anunciado do produto),
    nunca por `total_amount` -- frete ainda não é conhecido/comparável de
    forma confiável entre lojas nesta TASK; a mensagem final deixa
    explícito que o valor não inclui frete."""

    mission_id: UUID
    first_offer_id: UUID
    first_observation_id: UUID
    first_amount: Decimal
    first_currency: str
    second_offer_id: UUID | None = None
    second_observation_id: UUID | None = None
    second_amount: Decimal | None = None
    second_currency: str | None = None

    def __post_init__(self) -> None:
        _validate_money(self.first_amount, self.first_currency)
        has_second = self.second_offer_id is not None
        second_complete = (
            self.second_observation_id is not None
            and self.second_amount is not None
            and self.second_currency is not None
        )
        if has_second != second_complete:
            raise EventCatalogError(
                "second offer fields must be complete or all absent"
            )
        if has_second:
            _validate_money(self.second_amount, self.second_currency)
            if self.second_amount < self.first_amount:
                raise EventCatalogError("first_amount must be the lowest of the two")
            if self.second_offer_id == self.first_offer_id:
                raise EventCatalogError("first and second offers must differ")


@dataclass(frozen=True, slots=True)
class MissionPrelistErrataPayload:
    """TASK-068: única correção permitida quando uma coleta posterior à
    pré-lista encontra uma oferta `MATCH` com preço (`amount`, sem frete)
    mais barato que a base já enviada -- `previous_lowest_amount` é
    `None` só quando a pré-lista original não teve nenhuma oferta para
    mostrar."""

    mission_id: UUID
    offer_id: UUID
    observation_id: UUID
    current_amount: Decimal
    currency: str
    previous_lowest_amount: Decimal | None

    def __post_init__(self) -> None:
        _validate_money(self.current_amount, self.currency)
        if self.previous_lowest_amount is not None:
            _validate_money(self.previous_lowest_amount, self.currency)
            if self.current_amount >= self.previous_lowest_amount:
                raise EventCatalogError(
                    "current_amount must be lower than previous_lowest_amount"
                )


@dataclass(frozen=True, slots=True)
class PrelistOfferPayload:
    """Snapshot mínimo e imutável de uma opção selecionada pela TASK-094."""

    offer_id: UUID
    observation_id: UUID
    store_id: UUID
    amount: Decimal
    total_amount: Decimal
    currency: str
    relevance: OfferRelevance
    condition: OfferCondition
    seller_kind: MarketplacePartyKind | None
    availability: Availability

    def __post_init__(self) -> None:
        _validate_money(self.amount, self.currency)
        _validate_money(self.total_amount, self.currency)
        if self.total_amount < self.amount:
            raise EventCatalogError("total_amount must not be lower than amount")
        if self.relevance not in {
            OfferRelevance.MATCH,
            OfferRelevance.POSSIBLE_MATCH,
        }:
            raise EventCatalogError("prelist offer must be relevant")
        if not isinstance(self.condition, OfferCondition):
            raise EventCatalogError("condition must use OfferCondition")
        if self.seller_kind is not None and not isinstance(
            self.seller_kind, MarketplacePartyKind
        ):
            raise EventCatalogError("seller_kind must use MarketplacePartyKind")
        if not isinstance(self.availability, Availability):
            raise EventCatalogError("availability must use Availability")


def _validate_prelist_offers(
    offers: tuple[PrelistOfferPayload, ...], *, one_store: UUID | None = None
) -> None:
    if not isinstance(offers, tuple) or not 1 <= len(offers) <= 20:
        raise EventCatalogError("prelist must contain between 1 and 20 offers")
    if any(not isinstance(item, PrelistOfferPayload) for item in offers):
        raise EventCatalogError("prelist offers must use PrelistOfferPayload")
    if len({item.offer_id for item in offers}) != len(offers):
        raise EventCatalogError("prelist offers must be unique")
    counts: dict[UUID, int] = {}
    for item in offers:
        counts[item.store_id] = counts.get(item.store_id, 0) + 1
        if counts[item.store_id] > 5:
            raise EventCatalogError("prelist allows at most five offers per store")
        if one_store is not None and item.store_id != one_store:
            raise EventCatalogError("errata offers must belong to corrected_store_id")


@dataclass(frozen=True, slots=True)
class MissionPrelistReadyV2Payload:
    mission_id: UUID
    offers: tuple[PrelistOfferPayload, ...]

    def __post_init__(self) -> None:
        _validate_prelist_offers(self.offers)


@dataclass(frozen=True, slots=True)
class MissionPrelistErrataV2Payload:
    mission_id: UUID
    previous_event_id: UUID
    corrected_store_id: UUID
    offers: tuple[PrelistOfferPayload, ...]

    def __post_init__(self) -> None:
        _validate_prelist_offers(self.offers, one_store=self.corrected_store_id)


@dataclass(frozen=True, slots=True)
class ProductVariantOptionPayload:
    product_id: UUID
    label: str

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise EventCatalogError("variant label must not be blank")


@dataclass(frozen=True, slots=True)
class MissionVariantsReadyPayload:
    mission_id: UUID
    variants: tuple[ProductVariantOptionPayload, ...]
    state_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.variants, tuple) or not 1 <= len(self.variants) <= 20:
            raise EventCatalogError("variants must contain between 1 and 20 items")
        if any(not isinstance(item, ProductVariantOptionPayload) for item in self.variants):
            raise EventCatalogError("variants must use ProductVariantOptionPayload")
        if len({item.product_id for item in self.variants}) != len(self.variants):
            raise EventCatalogError("variants must be unique")
        if type(self.state_version) is not int or self.state_version < 0:
            raise EventCatalogError("state_version must be non-negative")


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
    | MissionPrelistReadyV2Payload
    | MissionPrelistErrataV2Payload
    | MissionVariantsReadyPayload
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
        EventType.MISSION_PRELIST_READY_V2: EventSpec(
            EventType.MISSION_PRELIST_READY_V2,
            AggregateType.MISSION,
            MissionPrelistReadyV2Payload,
        ),
        EventType.MISSION_PRELIST_ERRATA_V2: EventSpec(
            EventType.MISSION_PRELIST_ERRATA_V2,
            AggregateType.MISSION,
            MissionPrelistErrataV2Payload,
        ),
        EventType.MISSION_VARIANTS_READY_V1: EventSpec(
            EventType.MISSION_VARIANTS_READY_V1,
            AggregateType.MISSION,
            MissionVariantsReadyPayload,
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
