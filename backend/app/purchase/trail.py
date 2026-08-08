"""Serviço transacional da confirmação persistente e de sua trilha."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.purchase.confirmation import (
    MissionOwnerMismatchForConfirmationError,
    PurchaseConfirmationDecision,
    PurchaseConfirmationError,
    PurchaseConfirmationRequest,
    PurchaseConfirmationResult,
    PurchaseConfirmationStaleReason,
    PurchaseConfirmationStatus,
    _build_purchase_confirmation_request,
    _evaluate_purchase_confirmation,
    _require_mission_owner,
)
from app.purchase.models import (
    PurchaseConfirmation,
    PurchaseTrailEntry,
    PurchaseTrailEntryType,
)

TERMINAL_UNIQUE_CONSTRAINT = "ux_purchase_trail_entries_terminal_confirmation"
_TERMINAL_TYPES = (
    PurchaseTrailEntryType.CONFIRMED,
    PurchaseTrailEntryType.CANCELLED,
    PurchaseTrailEntryType.STALE,
)


class PurchaseConfirmationNotFoundError(PurchaseConfirmationError):
    pass


class PurchaseConfirmationConflictError(PurchaseConfirmationError):
    pass


def request_purchase_confirmation(
    session: Session,
    *,
    mission_id: UUID,
    offer_id: UUID,
    owner_user_id: UUID,
    now: datetime | None = None,
) -> PurchaseConfirmationRequest:
    """Persiste a solicitação e sua entrada requested na transação do chamador."""
    request = _build_purchase_confirmation_request(
        session,
        mission_id=mission_id,
        offer_id=offer_id,
        owner_user_id=owner_user_id,
        now=now,
    )
    session.add_all(
        (
            _confirmation_model(request),
            _trail_model(
                request,
                entry_type=PurchaseTrailEntryType.REQUESTED,
                decision=None,
                stale_reason=None,
                resolved_at=None,
            ),
        )
    )
    session.flush()
    return request


def recover_purchase_confirmation(
    session: Session,
    confirmation_id: UUID,
    *,
    owner_user_id: UUID,
) -> PurchaseConfirmationRequest:
    """Reconstrói a solicitação imutável após reinício do processo."""
    confirmation = session.get(PurchaseConfirmation, confirmation_id)
    if confirmation is None:
        raise PurchaseConfirmationNotFoundError("purchase confirmation was not found")
    if confirmation.owner_user_id != owner_user_id:
        raise MissionOwnerMismatchForConfirmationError(
            "only the mission owner can access the confirmation"
        )
    return _request_from_model(confirmation)


def resolve_purchase_confirmation(
    session: Session,
    confirmation_id: UUID,
    *,
    owner_user_id: UUID,
    decision: PurchaseConfirmationDecision,
    now: datetime | None = None,
) -> PurchaseConfirmationResult:
    """Acrescenta no máximo um terminal, com idempotência sob concorrência."""
    if not isinstance(decision, PurchaseConfirmationDecision):
        raise TypeError("decision must be a PurchaseConfirmationDecision")

    request = recover_purchase_confirmation(
        session,
        confirmation_id,
        owner_user_id=owner_user_id,
    )
    existing = _terminal_entry(session, confirmation_id)
    if existing is not None:
        return _existing_terminal_result(request, existing, decision)

    result = _evaluate_purchase_confirmation(
        session,
        request,
        owner_user_id=owner_user_id,
        decision=decision,
        now=now,
    )
    terminal = _trail_model(
        request,
        entry_type=_entry_type_for_status(result.status),
        decision=result.decision,
        stale_reason=result.stale_reason,
        resolved_at=result.resolved_at,
    )
    try:
        with session.begin_nested():
            session.add(terminal)
            session.flush()
    except IntegrityError as exc:
        if not _is_terminal_unique_violation(exc):
            raise
        winner = _terminal_entry(session, confirmation_id)
        if winner is None:
            raise
        return _existing_terminal_result(request, winner, decision)
    return result


def get_purchase_terminal_entry(
    session: Session,
    confirmation_id: UUID,
    *,
    owner_user_id: UUID,
) -> PurchaseTrailEntry | None:
    recover_purchase_confirmation(
        session,
        confirmation_id,
        owner_user_id=owner_user_id,
    )
    return _terminal_entry(session, confirmation_id)


def list_purchase_trail_entries(
    session: Session,
    mission_id: UUID,
    *,
    owner_user_id: UUID,
) -> tuple[PurchaseTrailEntry, ...]:
    """Lista a trilha da missão, limitada ao seu proprietário."""
    _require_mission_owner(session, mission_id, owner_user_id)
    return tuple(
        session.scalars(
            select(PurchaseTrailEntry)
            .where(
                PurchaseTrailEntry.mission_id == mission_id,
                PurchaseTrailEntry.owner_user_id == owner_user_id,
            )
            .order_by(PurchaseTrailEntry.recorded_at, PurchaseTrailEntry.id)
        )
    )


def _confirmation_model(request: PurchaseConfirmationRequest) -> PurchaseConfirmation:
    return PurchaseConfirmation(
        id=request.confirmation_id,
        mission_id=request.mission_id,
        owner_user_id=request.owner_user_id,
        offer_id=request.offer_id,
        price_observation_id=request.price_observation_id,
        product_id=request.product_id,
        store_id=request.store_id,
        seller_id=request.seller_id,
        position=request.position,
        url=request.url,
        amount=request.amount,
        shipping_amount=request.shipping_amount,
        total_amount=request.total_amount,
        currency=request.currency,
        availability=request.availability,
        fulfillment=request.fulfillment,
        observed_at=request.observed_at,
        evidence_snapshot=_sanitized_snapshot(request),
        requested_at=request.requested_at,
        expires_at=request.expires_at,
    )


def _sanitized_snapshot(request: PurchaseConfirmationRequest) -> dict[str, Any]:
    return {
        "product": {
            "id": str(request.product_id),
            "name": request.product_name,
            "brand": request.product_brand,
            "model": request.product_model,
        },
        "store": {
            "id": str(request.store_id),
            "code": request.store_code,
            "name": request.store_name,
        },
        "seller": (
            None
            if request.seller_id is None
            else {"id": str(request.seller_id), "name": request.seller_name}
        ),
        "offer": {
            "id": str(request.offer_id),
            "url": request.url,
            "position": request.position,
        },
        "observation": {
            "id": str(request.price_observation_id),
            "amount": str(request.amount),
            "shipping_amount": str(request.shipping_amount),
            "total_amount": str(request.total_amount),
            "currency": request.currency,
            "availability": request.availability.value,
            "fulfillment": request.fulfillment,
            "observed_at": request.observed_at.isoformat(),
        },
    }


def _request_from_model(model: PurchaseConfirmation) -> PurchaseConfirmationRequest:
    snapshot = model.evidence_snapshot
    product = snapshot["product"]
    store = snapshot["store"]
    seller = snapshot["seller"]
    return PurchaseConfirmationRequest(
        confirmation_id=model.id,
        mission_id=model.mission_id,
        owner_user_id=model.owner_user_id,
        offer_id=model.offer_id,
        price_observation_id=model.price_observation_id,
        position=model.position,
        product_id=model.product_id,
        product_name=product["name"],
        product_brand=product["brand"],
        product_model=product["model"],
        store_id=model.store_id,
        store_code=store["code"],
        store_name=store["name"],
        seller_id=model.seller_id,
        seller_name=None if seller is None else seller["name"],
        url=model.url,
        amount=model.amount,
        shipping_amount=model.shipping_amount,
        total_amount=model.total_amount,
        currency=model.currency,
        availability=model.availability,
        fulfillment=model.fulfillment,
        observed_at=model.observed_at,
        requested_at=model.requested_at,
        expires_at=model.expires_at,
    )


def _trail_model(
    request: PurchaseConfirmationRequest,
    *,
    entry_type: PurchaseTrailEntryType,
    decision: PurchaseConfirmationDecision | None,
    stale_reason: PurchaseConfirmationStaleReason | None,
    resolved_at: datetime | None,
) -> PurchaseTrailEntry:
    return PurchaseTrailEntry(
        confirmation_id=request.confirmation_id,
        mission_id=request.mission_id,
        owner_user_id=request.owner_user_id,
        offer_id=request.offer_id,
        price_observation_id=request.price_observation_id,
        entry_type=entry_type,
        decision=decision,
        stale_reason=stale_reason,
        resolved_at=resolved_at,
    )


def _terminal_entry(
    session: Session, confirmation_id: UUID
) -> PurchaseTrailEntry | None:
    return session.scalar(
        select(PurchaseTrailEntry).where(
            PurchaseTrailEntry.confirmation_id == confirmation_id,
            PurchaseTrailEntry.entry_type.in_(_TERMINAL_TYPES),
        )
    )


def _existing_terminal_result(
    request: PurchaseConfirmationRequest,
    entry: PurchaseTrailEntry,
    decision: PurchaseConfirmationDecision,
) -> PurchaseConfirmationResult:
    if entry.decision != decision:
        raise PurchaseConfirmationConflictError(
            "confirmation already has a conflicting terminal decision"
        )
    if entry.resolved_at is None:
        raise AssertionError("terminal purchase trail entry lacks resolved_at")
    return PurchaseConfirmationResult(
        request=request,
        decision=entry.decision,
        status=PurchaseConfirmationStatus(entry.entry_type.value),
        stale_reason=entry.stale_reason,
        resolved_at=entry.resolved_at,
    )


def _entry_type_for_status(
    status: PurchaseConfirmationStatus,
) -> PurchaseTrailEntryType:
    return PurchaseTrailEntryType(status.value)


def _is_terminal_unique_violation(exc: IntegrityError) -> bool:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == TERMINAL_UNIQUE_CONSTRAINT
