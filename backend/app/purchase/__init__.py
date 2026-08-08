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
from app.purchase.trail import (
    PurchaseConfirmationConflictError,
    PurchaseConfirmationNotFoundError,
    get_purchase_terminal_entry,
    list_purchase_trail_entries,
    recover_purchase_confirmation,
    request_purchase_confirmation,
    resolve_purchase_confirmation,
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
    "PurchaseConfirmationConflictError",
    "PurchaseConfirmationDecision",
    "PurchaseConfirmationError",
    "PurchaseConfirmationNotFoundError",
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
    "get_purchase_terminal_entry",
    "list_purchase_trail_entries",
    "rank_eligible_evidence",
    "recommend_for_mission",
    "recover_purchase_confirmation",
    "request_purchase_confirmation",
    "resolve_purchase_confirmation",
]
