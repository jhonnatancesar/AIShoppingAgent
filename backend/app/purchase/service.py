"""Seleção determinística de uma recomendação a partir do histórico persistido."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.collection.models import (
    CollectionRun,
    CollectionRunStatus,
    PriceObservation,
)
from app.collection.normalization import Availability
from app.missions.models import (
    Mission,
    MissionCriteria,
    MissionSource,
    MissionStatus,
)
from app.offers.models import Offer
from app.products.models import Product
from app.purchase.contracts import (
    HistoricalPriceEvidence,
    OfferRecommendationEvidence,
    RecommendationExclusion,
    RecommendationReason,
    RecommendationResult,
    RecommendationStatus,
)
from app.stores.models import Seller, Store


class RecommendationError(ValueError):
    """Erro de domínio que impede iniciar uma recomendação."""


class MissionNotFoundForRecommendationError(RecommendationError):
    pass


class MissionNotActiveForRecommendationError(RecommendationError):
    pass


@dataclass(frozen=True, slots=True)
class _ObservationRow:
    observation: PriceObservation
    offer: Offer
    product: Product
    store: Store
    seller: Seller | None


def recommend_for_mission(session: Session, mission_id: UUID) -> RecommendationResult:
    """Retorna a oferta elegível de menor custo total para uma missão ativa."""
    mission_row = session.execute(
        select(Mission, MissionCriteria)
        .outerjoin(MissionCriteria, MissionCriteria.mission_id == Mission.id)
        .where(Mission.id == mission_id)
    ).one_or_none()
    if mission_row is None:
        raise MissionNotFoundForRecommendationError("mission was not found")
    mission, criteria = mission_row
    if mission.status is not MissionStatus.ACTIVE:
        raise MissionNotActiveForRecommendationError("mission must be active")

    currency = criteria.target_currency if criteria is not None else None
    rows = _load_observation_rows(session, mission_id)
    evidence = _build_evidence(rows, currency)
    reason = _insufficient_reason(evidence, currency)
    if reason is not None:
        return RecommendationResult(
            mission_id=mission_id,
            status=RecommendationStatus.INSUFFICIENT_DATA,
            currency=currency,
            recommendation=None,
            reason=reason,
            evidence=evidence,
        )

    recommendation = rank_eligible_evidence(evidence)[0]
    return RecommendationResult(
        mission_id=mission_id,
        status=RecommendationStatus.RECOMMENDED,
        currency=currency,
        recommendation=recommendation,
        reason=None,
        evidence=evidence,
    )


def rank_eligible_evidence(
    evidence: Sequence[OfferRecommendationEvidence],
) -> tuple[OfferRecommendationEvidence, ...]:
    """Aplica a única ordem permitida para recomendação e comparação."""
    return tuple(
        sorted(
            (item for item in evidence if item.eligible),
            key=lambda item: (
                item.total_amount,
                -item.observed_at.timestamp(),
                str(item.offer_id),
            ),
        )
    )


def _load_observation_rows(
    session: Session, mission_id: UUID
) -> tuple[_ObservationRow, ...]:
    statement = (
        select(PriceObservation, Offer, Product, Store, Seller)
        .join(CollectionRun, CollectionRun.id == PriceObservation.collection_run_id)
        .join(Offer, Offer.id == PriceObservation.offer_id)
        .join(Product, Product.id == Offer.product_id)
        .join(Store, Store.id == Offer.store_id)
        .outerjoin(Seller, Seller.id == Offer.seller_id)
        .join(
            MissionSource,
            and_(
                MissionSource.mission_id == mission_id,
                MissionSource.store_id == Store.id,
            ),
        )
        .where(
            CollectionRun.mission_id == mission_id,
            CollectionRun.status == CollectionRunStatus.SUCCEEDED,
            CollectionRun.store_id == Store.id,
        )
        .order_by(
            Offer.id.asc(),
            PriceObservation.observed_at.desc(),
            PriceObservation.id.asc(),
        )
    )
    return tuple(_ObservationRow(*row) for row in session.execute(statement).all())


def _build_evidence(
    rows: Sequence[_ObservationRow], currency: str | None
) -> tuple[OfferRecommendationEvidence, ...]:
    by_offer: dict[UUID, list[_ObservationRow]] = defaultdict(list)
    for row in rows:
        by_offer[row.offer.id].append(row)

    evidence = [
        _offer_evidence(offer_rows, currency)
        for _, offer_rows in sorted(by_offer.items(), key=lambda item: str(item[0]))
    ]
    return tuple(evidence)


def _offer_evidence(
    rows: Sequence[_ObservationRow], currency: str | None
) -> OfferRecommendationEvidence:
    latest = rows[0]
    observation = latest.observation
    exclusions: list[RecommendationExclusion] = []
    if currency is None:
        exclusions.append(RecommendationExclusion.MISSION_CURRENCY_MISSING)
    if observation.availability is not Availability.AVAILABLE:
        exclusions.append(RecommendationExclusion.UNAVAILABLE)
    if currency is not None and observation.currency != currency:
        exclusions.append(RecommendationExclusion.CURRENCY_MISMATCH)
    if observation.shipping_amount is None:
        exclusions.append(RecommendationExclusion.SHIPPING_UNKNOWN)

    comparable = tuple(
        row.observation
        for row in rows
        if currency is not None
        and row.observation.currency == currency
        and row.observation.availability is Availability.AVAILABLE
        and row.observation.shipping_amount is not None
    )
    previous = next(
        (
            row.observation
            for row in rows[1:]
            if currency is not None
            and row.observation.currency == currency
            and row.observation.availability is Availability.AVAILABLE
            and row.observation.shipping_amount is not None
        ),
        None,
    )
    lowest = min(
        comparable,
        key=lambda item: (item.total_amount, item.observed_at, str(item.id)),
        default=None,
    )
    history = HistoricalPriceEvidence(
        observation_count=len(rows),
        comparable_observation_count=len(comparable),
        previous_observation_id=previous.id if previous is not None else None,
        previous_total_amount=(previous.total_amount if previous is not None else None),
        previous_observed_at=previous.observed_at if previous is not None else None,
        lowest_observation_id=lowest.id if lowest is not None else None,
        lowest_total_amount=lowest.total_amount if lowest is not None else None,
        lowest_observed_at=lowest.observed_at if lowest is not None else None,
    )
    return OfferRecommendationEvidence(
        offer_id=latest.offer.id,
        product_id=latest.product.id,
        product_name=latest.product.name,
        product_brand=latest.product.brand,
        product_model=latest.product.model,
        store_id=latest.store.id,
        store_code=latest.store.code,
        store_name=latest.store.name,
        seller_id=latest.seller.id if latest.seller is not None else None,
        seller_name=latest.seller.name if latest.seller is not None else None,
        url=latest.offer.url,
        observation_id=observation.id,
        amount=observation.amount,
        shipping_amount=observation.shipping_amount,
        total_amount=observation.total_amount,
        currency=observation.currency,
        fulfillment=observation.fulfillment,
        availability=observation.availability,
        observed_at=observation.observed_at,
        recorded_at=observation.recorded_at,
        history=history,
        eligible=not exclusions,
        exclusions=tuple(exclusions),
    )


def _insufficient_reason(
    evidence: Sequence[OfferRecommendationEvidence], currency: str | None
) -> RecommendationReason | None:
    if currency is None:
        return RecommendationReason.MISSION_CURRENCY_MISSING
    if not evidence:
        return RecommendationReason.NO_OBSERVATIONS
    available = tuple(
        item for item in evidence if item.availability is Availability.AVAILABLE
    )
    if not available:
        return RecommendationReason.NO_AVAILABLE_OFFERS
    compatible = tuple(item for item in available if item.currency == currency)
    if not compatible:
        return RecommendationReason.NO_CURRENCY_COMPATIBLE_OFFERS
    if not any(item.shipping_amount is not None for item in compatible):
        return RecommendationReason.NO_DETERMINABLE_TOTALS
    return None
