"""Comparação ordenada que reutiliza a recomendação da TASK-038."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.collection.normalization import Availability
from app.purchase.contracts import (
    HistoricalPriceEvidence,
    OfferRecommendationEvidence,
    RecommendationExclusion,
    RecommendationReason,
    RecommendationStatus,
)
from app.purchase.service import rank_eligible_evidence, recommend_for_mission


@dataclass(frozen=True, slots=True)
class OfferComparisonItem:
    """Uma oferta comparável ou uma evidência inelegível sem posição."""

    position: int | None
    offer_id: UUID
    product_id: UUID
    product_name: str
    product_brand: str | None
    product_model: str | None
    store_id: UUID
    store_code: str
    store_name: str
    seller_id: UUID | None
    seller_name: str | None
    url: str
    observation_id: UUID
    amount: Decimal
    shipping_amount: Decimal | None
    total_amount: Decimal | None
    currency: str
    fulfillment: str | None
    availability: Availability
    observed_at: datetime
    recorded_at: datetime
    history: HistoricalPriceEvidence
    eligible: bool
    exclusions: tuple[RecommendationExclusion, ...]

    def __post_init__(self) -> None:
        if self.shipping_amount is None and self.total_amount is not None:
            raise ValueError("unknown shipping requires total_amount=None")
        if self.eligible:
            if self.position is None or self.position < 1:
                raise ValueError("eligible comparison item requires a position")
            if self.total_amount is None or self.exclusions:
                raise ValueError("eligible comparison item must have a valid total")
            return
        if self.position is not None or not self.exclusions:
            raise ValueError(
                "ineligible comparison item must be unranked and explained"
            )


@dataclass(frozen=True, slots=True)
class OfferComparisonResult:
    """Ranking completo das elegíveis seguido pelas evidências inelegíveis."""

    mission_id: UUID
    status: RecommendationStatus
    currency: str | None
    recommendation_offer_id: UUID | None
    reason: RecommendationReason | None
    items: tuple[OfferComparisonItem, ...]

    def __post_init__(self) -> None:
        eligible = tuple(item for item in self.items if item.eligible)
        positions = tuple(item.position for item in eligible)
        if positions != tuple(range(1, len(eligible) + 1)):
            raise ValueError("eligible positions must be consecutive")
        if self.items[: len(eligible)] != eligible:
            raise ValueError("ineligible items must follow eligible items")
        if self.status is RecommendationStatus.RECOMMENDED:
            if not eligible or self.reason is not None:
                raise ValueError("recommended comparison requires ranked items")
            if self.recommendation_offer_id != eligible[0].offer_id:
                raise ValueError("position one must match the recommendation")
            return
        if eligible or self.recommendation_offer_id is not None or self.reason is None:
            raise ValueError("insufficient comparison cannot contain ranked offers")


def compare_offers_for_mission(
    session: Session,
    mission_id: UUID,
    *,
    owner_user_id: UUID,
) -> OfferComparisonResult:
    """Compara todas as evidências sem redefinir elegibilidade ou recomendação."""
    recommendation = recommend_for_mission(
        session,
        mission_id,
        owner_user_id=owner_user_id,
    )
    ranked = rank_eligible_evidence(recommendation.evidence)
    eligible_items = tuple(
        _comparison_item(item, position=position)
        for position, item in enumerate(ranked, start=1)
    )
    ineligible_items = tuple(
        _comparison_item(item, position=None)
        for item in sorted(
            (item for item in recommendation.evidence if not item.eligible),
            key=lambda item: (
                item.store_code.casefold(),
                item.product_name.casefold(),
                str(item.offer_id),
            ),
        )
    )
    return OfferComparisonResult(
        mission_id=mission_id,
        status=recommendation.status,
        currency=recommendation.currency,
        recommendation_offer_id=(
            recommendation.recommendation.offer_id
            if recommendation.recommendation is not None
            else None
        ),
        reason=recommendation.reason,
        items=eligible_items + ineligible_items,
    )


def _comparison_item(
    evidence: OfferRecommendationEvidence, *, position: int | None
) -> OfferComparisonItem:
    return OfferComparisonItem(
        position=position,
        offer_id=evidence.offer_id,
        product_id=evidence.product_id,
        product_name=evidence.product_name,
        product_brand=evidence.product_brand,
        product_model=evidence.product_model,
        store_id=evidence.store_id,
        store_code=evidence.store_code,
        store_name=evidence.store_name,
        seller_id=evidence.seller_id,
        seller_name=evidence.seller_name,
        url=evidence.url,
        observation_id=evidence.observation_id,
        amount=evidence.amount,
        shipping_amount=evidence.shipping_amount,
        total_amount=(
            evidence.total_amount if evidence.shipping_amount is not None else None
        ),
        currency=evidence.currency,
        fulfillment=evidence.fulfillment,
        availability=evidence.availability,
        observed_at=evidence.observed_at,
        recorded_at=evidence.recorded_at,
        history=evidence.history,
        eligible=evidence.eligible,
        exclusions=evidence.exclusions,
    )
