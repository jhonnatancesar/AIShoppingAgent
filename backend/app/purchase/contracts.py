"""Contratos imutáveis do fluxo determinístico de recomendação."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.collection.normalization import Availability


class RecommendationStatus(StrEnum):
    RECOMMENDED = "recommended"
    INSUFFICIENT_DATA = "insufficient_data"


class RecommendationReason(StrEnum):
    MISSION_CURRENCY_MISSING = "mission_currency_missing"
    NO_OBSERVATIONS = "no_observations"
    NO_AVAILABLE_OFFERS = "no_available_offers"
    NO_CURRENCY_COMPATIBLE_OFFERS = "no_currency_compatible_offers"
    NO_DETERMINABLE_TOTALS = "no_determinable_totals"


class RecommendationExclusion(StrEnum):
    MISSION_CURRENCY_MISSING = "mission_currency_missing"
    UNAVAILABLE = "unavailable"
    CURRENCY_MISMATCH = "currency_mismatch"
    SHIPPING_UNKNOWN = "shipping_unknown"


@dataclass(frozen=True, slots=True)
class HistoricalPriceEvidence:
    """Resumo identificável do histórico comparável da oferta na missão."""

    observation_count: int
    comparable_observation_count: int
    previous_observation_id: UUID | None
    previous_total_amount: Decimal | None
    previous_observed_at: datetime | None
    lowest_observation_id: UUID | None
    lowest_total_amount: Decimal | None
    lowest_observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class OfferRecommendationEvidence:
    """Estado corrente e histórico suficiente para explicar uma candidatura."""

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
    total_amount: Decimal
    currency: str
    fulfillment: str | None
    availability: Availability
    observed_at: datetime
    recorded_at: datetime
    history: HistoricalPriceEvidence
    eligible: bool
    exclusions: tuple[RecommendationExclusion, ...]


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    """Uma recomendação única ou uma insuficiência de dados explicitada."""

    mission_id: UUID
    status: RecommendationStatus
    currency: str | None
    recommendation: OfferRecommendationEvidence | None
    reason: RecommendationReason | None
    evidence: tuple[OfferRecommendationEvidence, ...]

    def __post_init__(self) -> None:
        if self.status is RecommendationStatus.RECOMMENDED:
            if self.recommendation is None or self.reason is not None:
                raise ValueError("recommended result requires one offer and no reason")
            if not self.recommendation.eligible:
                raise ValueError("recommended offer must be eligible")
            if self.recommendation not in self.evidence:
                raise ValueError("recommended offer must be present in evidence")
            return
        if self.recommendation is not None or self.reason is None:
            raise ValueError(
                "insufficient_data result requires a reason and no recommendation"
            )
