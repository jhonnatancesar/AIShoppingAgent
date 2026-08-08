"""Testes da confirmação temporária e sem efeitos financeiros da TASK-040."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import app.purchase.confirmation as confirmation_module
import pytest
from app.collection.normalization import Availability
from app.purchase import (
    PURCHASE_CONFIRMATION_TTL,
    HistoricalPriceEvidence,
    MissionNotFoundForConfirmationError,
    MissionOwnerMismatchForConfirmationError,
    OfferComparisonItem,
    OfferComparisonResult,
    OfferNotEligibleForConfirmationError,
    PurchaseConfirmationDecision,
    PurchaseConfirmationStaleReason,
    PurchaseConfirmationStatus,
    RecommendationExclusion,
    RecommendationReason,
    RecommendationStatus,
)

request_purchase_confirmation = confirmation_module._build_purchase_confirmation_request
resolve_purchase_confirmation = confirmation_module._evaluate_purchase_confirmation

NOW = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
MISSION_ID = UUID(int=1)
OWNER_ID = UUID(int=2)
OTHER_USER_ID = UUID(int=3)


class _Session:
    def __init__(self, *, mission_id: UUID = MISSION_ID, owner_id: UUID = OWNER_ID):
        self.mission_id = mission_id
        self.mission = SimpleNamespace(id=mission_id, user_id=owner_id)

    def get(self, model, identifier):
        del model
        return self.mission if identifier == self.mission_id else None


def _history() -> HistoricalPriceEvidence:
    return HistoricalPriceEvidence(
        observation_count=1,
        comparable_observation_count=1,
        previous_observation_id=None,
        previous_total_amount=None,
        previous_observed_at=None,
        lowest_observation_id=UUID(int=101),
        lowest_total_amount=Decimal("110"),
        lowest_observed_at=NOW,
    )


def _eligible_item(index: int, *, position: int) -> OfferComparisonItem:
    return OfferComparisonItem(
        position=position,
        offer_id=UUID(int=1000 + index),
        product_id=UUID(int=2000 + index),
        product_name=f"Produto {index}",
        product_brand="Marca",
        product_model="Modelo",
        store_id=UUID(int=3000 + index),
        store_code=f"store_{index}",
        store_name=f"Store {index}",
        seller_id=None,
        seller_name=None,
        url=f"https://example.test/{index}",
        observation_id=UUID(int=4000 + index),
        amount=Decimal("100"),
        shipping_amount=Decimal("10"),
        total_amount=Decimal("110"),
        currency="BRL",
        fulfillment="Loja",
        availability=Availability.AVAILABLE,
        observed_at=NOW - timedelta(minutes=index),
        recorded_at=NOW,
        history=_history(),
        eligible=True,
        exclusions=(),
    )


def _comparison(*items: OfferComparisonItem) -> OfferComparisonResult:
    eligible = tuple(item for item in items if item.eligible)
    return OfferComparisonResult(
        mission_id=MISSION_ID,
        status=(
            RecommendationStatus.RECOMMENDED
            if eligible
            else RecommendationStatus.INSUFFICIENT_DATA
        ),
        currency="BRL",
        recommendation_offer_id=eligible[0].offer_id if eligible else None,
        reason=None if eligible else RecommendationReason.NO_DETERMINABLE_TOTALS,
        items=items,
    )


def _request(
    monkeypatch,
    *items: OfferComparisonItem,
    offer_id: UUID | None = None,
    now: datetime = NOW,
):
    comparison = _comparison(*items)
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: comparison,
    )
    selected_id = offer_id if offer_id is not None else items[0].offer_id
    return request_purchase_confirmation(
        _Session(),
        mission_id=MISSION_ID,
        offer_id=selected_id,
        owner_user_id=OWNER_ID,
        now=now,
    )


def test_request_binds_owner_offer_and_exact_observation_with_explicit_ttl(
    monkeypatch,
) -> None:
    first = _eligible_item(1, position=1)
    selected = _eligible_item(2, position=2)

    request = _request(monkeypatch, first, selected, offer_id=selected.offer_id)

    assert request.mission_id == MISSION_ID
    assert request.owner_user_id == OWNER_ID
    assert request.offer_id == selected.offer_id
    assert request.price_observation_id == selected.observation_id
    assert request.position == 2
    assert request.amount == Decimal("100")
    assert request.shipping_amount == Decimal("10")
    assert request.total_amount == Decimal("110")
    assert request.requested_at == NOW
    assert request.expires_at == NOW + PURCHASE_CONFIRMATION_TTL


def test_request_rejects_a_different_owner(monkeypatch) -> None:
    item = _eligible_item(1, position=1)
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: _comparison(item),
    )

    with pytest.raises(MissionOwnerMismatchForConfirmationError):
        request_purchase_confirmation(
            _Session(),
            mission_id=MISSION_ID,
            offer_id=item.offer_id,
            owner_user_id=OTHER_USER_ID,
            now=NOW,
        )


def test_request_rejects_a_missing_mission() -> None:
    with pytest.raises(MissionNotFoundForConfirmationError):
        request_purchase_confirmation(
            _Session(mission_id=UUID(int=99)),
            mission_id=MISSION_ID,
            offer_id=UUID(int=1001),
            owner_user_id=OWNER_ID,
            now=NOW,
        )


def test_request_rejects_an_ineligible_offer(monkeypatch) -> None:
    ineligible = replace(
        _eligible_item(1, position=1),
        position=None,
        shipping_amount=None,
        total_amount=None,
        eligible=False,
        exclusions=(RecommendationExclusion.SHIPPING_UNKNOWN,),
    )
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: _comparison(ineligible),
    )

    with pytest.raises(OfferNotEligibleForConfirmationError):
        request_purchase_confirmation(
            _Session(),
            mission_id=MISSION_ID,
            offer_id=ineligible.offer_id,
            owner_user_id=OWNER_ID,
            now=NOW,
        )


def test_confirmation_succeeds_only_for_the_same_current_evidence(monkeypatch) -> None:
    item = _eligible_item(1, position=1)
    request = _request(monkeypatch, item)

    result = resolve_purchase_confirmation(
        _Session(),
        request,
        owner_user_id=OWNER_ID,
        decision=PurchaseConfirmationDecision.CONFIRM,
        now=NOW + timedelta(minutes=1),
    )

    assert result.status is PurchaseConfirmationStatus.CONFIRMED
    assert result.stale_reason is None
    assert result.request is request


def test_cancellation_is_terminal_without_revalidating_evidence(monkeypatch) -> None:
    item = _eligible_item(1, position=1)
    request = _request(monkeypatch, item)
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: pytest.fail(
            "cancel must not recalculate"
        ),
    )

    result = resolve_purchase_confirmation(
        _Session(),
        request,
        owner_user_id=OWNER_ID,
        decision=PurchaseConfirmationDecision.CANCEL,
        now=NOW + timedelta(minutes=1),
    )

    assert result.status is PurchaseConfirmationStatus.CANCELLED
    assert result.stale_reason is None


def test_expired_confirm_is_stale_without_recalculating(monkeypatch) -> None:
    item = _eligible_item(1, position=1)
    request = _request(monkeypatch, item)
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: pytest.fail(
            "expired request must not recalculate"
        ),
    )

    result = resolve_purchase_confirmation(
        _Session(),
        request,
        owner_user_id=OWNER_ID,
        decision=PurchaseConfirmationDecision.CONFIRM,
        now=NOW + PURCHASE_CONFIRMATION_TTL,
    )

    assert result.status is PurchaseConfirmationStatus.STALE
    assert result.stale_reason is PurchaseConfirmationStaleReason.EXPIRED


def test_expired_request_can_still_be_cancelled(monkeypatch) -> None:
    item = _eligible_item(1, position=1)
    request = _request(monkeypatch, item)
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: pytest.fail(
            "cancel must not recalculate"
        ),
    )

    result = resolve_purchase_confirmation(
        _Session(),
        request,
        owner_user_id=OWNER_ID,
        decision=PurchaseConfirmationDecision.CANCEL,
        now=NOW + timedelta(minutes=16),
    )

    assert result.status is PurchaseConfirmationStatus.CANCELLED
    assert result.stale_reason is None


@pytest.mark.parametrize(
    "changed_item",
    [
        replace(_eligible_item(1, position=1), amount=Decimal("99")),
        replace(_eligible_item(1, position=1), shipping_amount=Decimal("11")),
        replace(_eligible_item(1, position=1), total_amount=Decimal("111")),
        replace(_eligible_item(1, position=1), currency="USD"),
    ],
    ids=["amount", "shipping", "total", "currency"],
)
def test_changed_evidence_is_stale(monkeypatch, changed_item) -> None:
    original = _eligible_item(1, position=1)
    request = _request(monkeypatch, original)
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: _comparison(changed_item),
    )

    result = resolve_purchase_confirmation(
        _Session(),
        request,
        owner_user_id=OWNER_ID,
        decision=PurchaseConfirmationDecision.CONFIRM,
        now=NOW + timedelta(minutes=1),
    )

    assert result.status is PurchaseConfirmationStatus.STALE
    assert result.stale_reason is PurchaseConfirmationStaleReason.EVIDENCE_CHANGED


def test_new_observation_with_equivalent_material_evidence_remains_valid(
    monkeypatch,
) -> None:
    original = _eligible_item(1, position=1)
    request = _request(monkeypatch, original)
    newer = replace(
        original,
        observation_id=UUID(int=9001),
        observed_at=NOW + timedelta(seconds=30),
        recorded_at=NOW + timedelta(seconds=31),
    )
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: _comparison(newer),
    )

    result = resolve_purchase_confirmation(
        _Session(),
        request,
        owner_user_id=OWNER_ID,
        decision=PurchaseConfirmationDecision.CONFIRM,
        now=NOW + timedelta(minutes=1),
    )

    assert result.status is PurchaseConfirmationStatus.CONFIRMED
    assert result.request.price_observation_id == original.observation_id


def test_offer_that_becomes_unavailable_is_stale(monkeypatch) -> None:
    original = _eligible_item(1, position=1)
    request = _request(monkeypatch, original)
    unavailable = replace(
        original,
        position=None,
        availability=Availability.UNAVAILABLE,
        eligible=False,
        exclusions=(RecommendationExclusion.UNAVAILABLE,),
    )
    monkeypatch.setattr(
        confirmation_module,
        "compare_offers_for_mission",
        lambda session, mission_id, *, owner_user_id: _comparison(unavailable),
    )

    result = resolve_purchase_confirmation(
        _Session(),
        request,
        owner_user_id=OWNER_ID,
        decision=PurchaseConfirmationDecision.CONFIRM,
        now=NOW + timedelta(minutes=1),
    )

    assert result.status is PurchaseConfirmationStatus.STALE
    assert result.stale_reason is PurchaseConfirmationStaleReason.EVIDENCE_CHANGED


def test_other_user_cannot_resolve_confirmation(monkeypatch) -> None:
    request = _request(monkeypatch, _eligible_item(1, position=1))

    with pytest.raises(MissionOwnerMismatchForConfirmationError):
        resolve_purchase_confirmation(
            _Session(),
            request,
            owner_user_id=OTHER_USER_ID,
            decision=PurchaseConfirmationDecision.CONFIRM,
            now=NOW + timedelta(minutes=1),
        )


def test_resolution_requires_typed_decision(monkeypatch) -> None:
    request = _request(monkeypatch, _eligible_item(1, position=1))

    with pytest.raises(TypeError):
        resolve_purchase_confirmation(
            _Session(),
            request,
            owner_user_id=OWNER_ID,
            decision="confirm",  # type: ignore[arg-type]
            now=NOW + timedelta(minutes=1),
        )


def test_request_rejects_a_naive_clock(monkeypatch) -> None:
    item = _eligible_item(1, position=1)

    with pytest.raises(ValueError, match="timezone-aware"):
        _request(
            monkeypatch,
            item,
            now=datetime(2026, 8, 8, 15, 0),
        )
