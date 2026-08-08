"""Confirmação temporária e sem efeitos financeiros da TASK-040."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.collection.normalization import Availability
from app.missions.models import Mission
from app.purchase.comparison import OfferComparisonItem, compare_offers_for_mission
from app.purchase.service import RecommendationError

PURCHASE_CONFIRMATION_TTL = timedelta(minutes=15)


class PurchaseConfirmationDecision(StrEnum):
    CONFIRM = "confirm"
    CANCEL = "cancel"


class PurchaseConfirmationStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    STALE = "stale"


class PurchaseConfirmationStaleReason(StrEnum):
    EXPIRED = "expired"
    EVIDENCE_CHANGED = "evidence_changed"


class PurchaseConfirmationError(ValueError):
    """Erro de domínio que impede criar ou resolver uma confirmação."""


class MissionNotFoundForConfirmationError(PurchaseConfirmationError):
    pass


class MissionOwnerMismatchForConfirmationError(PurchaseConfirmationError):
    pass


class OfferNotEligibleForConfirmationError(PurchaseConfirmationError):
    pass


@dataclass(frozen=True, slots=True)
class PurchaseConfirmationRequest:
    """Snapshot temporário vinculado à evidência exata escolhida."""

    confirmation_id: UUID
    mission_id: UUID
    owner_user_id: UUID
    offer_id: UUID
    price_observation_id: UUID
    position: int
    product_id: UUID
    product_name: str
    store_id: UUID
    store_code: str
    store_name: str
    seller_id: UUID | None
    seller_name: str | None
    url: str
    amount: Decimal
    shipping_amount: Decimal
    total_amount: Decimal
    currency: str
    availability: Availability
    fulfillment: str | None
    observed_at: datetime
    requested_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")
        _require_utc(self.requested_at, "requested_at")
        _require_utc(self.expires_at, "expires_at")
        if self.expires_at - self.requested_at != PURCHASE_CONFIRMATION_TTL:
            raise ValueError("purchase confirmation must use the configured TTL")
        if self.position < 1:
            raise ValueError("purchase confirmation requires an eligible position")
        if self.availability is not Availability.AVAILABLE:
            raise ValueError("purchase confirmation requires an available offer")
        if self.shipping_amount is None or self.total_amount is None:
            raise ValueError("purchase confirmation requires a determinable total")


@dataclass(frozen=True, slots=True)
class PurchaseConfirmationResult:
    """Resultado sem persistência, compra, checkout ou efeito financeiro."""

    request: PurchaseConfirmationRequest
    decision: PurchaseConfirmationDecision
    status: PurchaseConfirmationStatus
    stale_reason: PurchaseConfirmationStaleReason | None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, PurchaseConfirmationDecision):
            raise TypeError("decision must be a PurchaseConfirmationDecision")
        if self.status is PurchaseConfirmationStatus.CONFIRMED:
            if (
                self.decision is not PurchaseConfirmationDecision.CONFIRM
                or self.stale_reason is not None
            ):
                raise ValueError("confirmed result requires a valid confirmation")
            return
        if self.status is PurchaseConfirmationStatus.CANCELLED:
            if (
                self.decision is not PurchaseConfirmationDecision.CANCEL
                or self.stale_reason is not None
            ):
                raise ValueError("cancelled result requires an explicit cancellation")
            return
        if self.status is not PurchaseConfirmationStatus.STALE:
            raise ValueError("purchase confirmation status is invalid")
        if self.stale_reason is None:
            raise ValueError("stale result requires a reason")


def request_purchase_confirmation(
    session: Session,
    *,
    mission_id: UUID,
    offer_id: UUID,
    owner_user_id: UUID,
    now: datetime | None = None,
) -> PurchaseConfirmationRequest:
    """Cria em memória uma solicitação para uma oferta atualmente elegível."""
    _require_mission_owner(session, mission_id, owner_user_id)
    comparison = compare_offers_for_mission(session, mission_id)
    selected = _eligible_offer(comparison.items, offer_id)
    if selected is None:
        raise OfferNotEligibleForConfirmationError(
            "offer must be eligible in the current comparison"
        )

    requested_at = _utc_now(now)
    if selected.position is None or selected.shipping_amount is None:
        raise AssertionError("eligible comparison evidence is incomplete")
    if selected.total_amount is None:
        raise AssertionError("eligible comparison total is unavailable")
    return PurchaseConfirmationRequest(
        confirmation_id=uuid4(),
        mission_id=mission_id,
        owner_user_id=owner_user_id,
        offer_id=selected.offer_id,
        price_observation_id=selected.observation_id,
        position=selected.position,
        product_id=selected.product_id,
        product_name=selected.product_name,
        store_id=selected.store_id,
        store_code=selected.store_code,
        store_name=selected.store_name,
        seller_id=selected.seller_id,
        seller_name=selected.seller_name,
        url=selected.url,
        amount=selected.amount,
        shipping_amount=selected.shipping_amount,
        total_amount=selected.total_amount,
        currency=selected.currency,
        availability=selected.availability,
        fulfillment=selected.fulfillment,
        observed_at=selected.observed_at,
        requested_at=requested_at,
        expires_at=requested_at + PURCHASE_CONFIRMATION_TTL,
    )


def resolve_purchase_confirmation(
    session: Session,
    request: PurchaseConfirmationRequest,
    *,
    owner_user_id: UUID,
    decision: PurchaseConfirmationDecision,
    now: datetime | None = None,
) -> PurchaseConfirmationResult:
    """Resolve somente após validar identidade, validade e evidência corrente."""
    if not isinstance(decision, PurchaseConfirmationDecision):
        raise TypeError("decision must be a PurchaseConfirmationDecision")
    if owner_user_id != request.owner_user_id:
        raise MissionOwnerMismatchForConfirmationError(
            "only the mission owner can resolve the confirmation"
        )

    resolved_at = _utc_now(now)
    if resolved_at >= request.expires_at:
        return _stale_result(
            request,
            decision,
            PurchaseConfirmationStaleReason.EXPIRED,
        )
    if decision is PurchaseConfirmationDecision.CANCEL:
        return PurchaseConfirmationResult(
            request=request,
            decision=decision,
            status=PurchaseConfirmationStatus.CANCELLED,
            stale_reason=None,
        )

    mission = session.get(Mission, request.mission_id)
    if mission is None or mission.user_id != request.owner_user_id:
        return _stale_result(
            request,
            decision,
            PurchaseConfirmationStaleReason.EVIDENCE_CHANGED,
        )
    try:
        comparison = compare_offers_for_mission(session, request.mission_id)
    except RecommendationError:
        return _stale_result(
            request,
            decision,
            PurchaseConfirmationStaleReason.EVIDENCE_CHANGED,
        )
    current = _eligible_offer(comparison.items, request.offer_id)
    if current is None or not _same_evidence(request, current):
        return _stale_result(
            request,
            decision,
            PurchaseConfirmationStaleReason.EVIDENCE_CHANGED,
        )
    return PurchaseConfirmationResult(
        request=request,
        decision=decision,
        status=PurchaseConfirmationStatus.CONFIRMED,
        stale_reason=None,
    )


def _require_mission_owner(
    session: Session, mission_id: UUID, owner_user_id: UUID
) -> Mission:
    mission = session.get(Mission, mission_id)
    if mission is None:
        raise MissionNotFoundForConfirmationError("mission was not found")
    if mission.user_id != owner_user_id:
        raise MissionOwnerMismatchForConfirmationError(
            "only the mission owner can request a confirmation"
        )
    return mission


def _eligible_offer(
    items: tuple[OfferComparisonItem, ...], offer_id: UUID
) -> OfferComparisonItem | None:
    return next(
        (item for item in items if item.offer_id == offer_id and item.eligible),
        None,
    )


def _same_evidence(
    request: PurchaseConfirmationRequest, current: OfferComparisonItem
) -> bool:
    return (
        current.offer_id == request.offer_id
        and current.observation_id == request.price_observation_id
        and current.availability is request.availability
        and current.currency == request.currency
        and current.amount == request.amount
        and current.shipping_amount == request.shipping_amount
        and current.total_amount == request.total_amount
    )


def _stale_result(
    request: PurchaseConfirmationRequest,
    decision: PurchaseConfirmationDecision,
    reason: PurchaseConfirmationStaleReason,
) -> PurchaseConfirmationResult:
    return PurchaseConfirmationResult(
        request=request,
        decision=decision,
        status=PurchaseConfirmationStatus.STALE,
        stale_reason=reason,
    )


def _utc_now(value: datetime | None) -> datetime:
    current = value if value is not None else datetime.now(UTC)
    _require_aware(current, "now")
    return current.astimezone(UTC)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_utc(value: datetime, field_name: str) -> None:
    _require_aware(value, field_name)
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
