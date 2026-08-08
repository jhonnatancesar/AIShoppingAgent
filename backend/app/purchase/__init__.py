"""Fluxo determinístico de recomendação de compra."""

from app.purchase.comparison import (
    OfferComparisonItem,
    OfferComparisonResult,
    compare_offers_for_mission,
)
from app.purchase.confirmation import (
    PURCHASE_CONFIRMATION_TTL,
    MissionNotFoundForConfirmationError,
    MissionOwnerMismatchForConfirmationError,
    OfferNotEligibleForConfirmationError,
    PurchaseConfirmationDecision,
    PurchaseConfirmationError,
    PurchaseConfirmationRequest,
    PurchaseConfirmationResult,
    PurchaseConfirmationStaleReason,
    PurchaseConfirmationStatus,
    request_purchase_confirmation,
    resolve_purchase_confirmation,
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
    "MissionNotFoundForConfirmationError",
    "MissionNotActiveForRecommendationError",
    "MissionNotFoundForRecommendationError",
    "MissionOwnerMismatchForConfirmationError",
    "OfferComparisonItem",
    "OfferComparisonResult",
    "OfferNotEligibleForConfirmationError",
    "OfferRecommendationEvidence",
    "PURCHASE_CONFIRMATION_TTL",
    "PurchaseConfirmationDecision",
    "PurchaseConfirmationError",
    "PurchaseConfirmationRequest",
    "PurchaseConfirmationResult",
    "PurchaseConfirmationStaleReason",
    "PurchaseConfirmationStatus",
    "RecommendationError",
    "RecommendationExclusion",
    "RecommendationReason",
    "RecommendationResult",
    "RecommendationStatus",
    "compare_offers_for_mission",
    "rank_eligible_evidence",
    "recommend_for_mission",
    "request_purchase_confirmation",
    "resolve_purchase_confirmation",
]
