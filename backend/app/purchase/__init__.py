"""Fluxo determinístico de recomendação de compra."""

from app.purchase.comparison import (
    OfferComparisonItem,
    OfferComparisonResult,
    compare_offers_for_mission,
)
from app.purchase.contracts import (
    HistoricalPriceEvidence,
    OfferRecommendationEvidence,
    RecommendationExclusion,
    RecommendationReason,
    RecommendationResult,
    RecommendationStatus,
)
from app.purchase.service import (
    MissionNotActiveForRecommendationError,
    MissionNotFoundForRecommendationError,
    RecommendationError,
    rank_eligible_evidence,
    recommend_for_mission,
)

__all__ = [
    "HistoricalPriceEvidence",
    "MissionNotActiveForRecommendationError",
    "MissionNotFoundForRecommendationError",
    "OfferComparisonItem",
    "OfferComparisonResult",
    "OfferRecommendationEvidence",
    "RecommendationError",
    "RecommendationExclusion",
    "RecommendationReason",
    "RecommendationResult",
    "RecommendationStatus",
    "compare_offers_for_mission",
    "rank_eligible_evidence",
    "recommend_for_mission",
]
