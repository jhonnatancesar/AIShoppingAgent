"""Contratos para coleta de ofertas em fontes selecionadas."""

from app.collection.adapter import CollectionAdapter
from app.collection.browser import BrowserSession, BrowserSettings
from app.collection.contracts import (
    CollectionProvider,
    CollectionRequest,
    CollectionResult,
    InstallmentInterestKind,
    MarketplacePartyKind,
    OfferCondition,
    ProductIdentityResolver,
    RawCollectedOffer,
    RawInstallmentOption,
    ResolvedProductIdentity,
)
from app.collection.errors import (
    CollectionContractError,
    CollectionError,
    CollectionNormalizationError,
    DuplicateProviderError,
    ProviderBlockedError,
    ProviderCircuitOpenError,
    ProviderNavigationError,
    UnsupportedSourceError,
)
from app.collection.history import (
    PriceHistoryPage,
    PriceHistoryQueryError,
    get_latest_price_observation,
    list_price_history,
)
from app.collection.identity_resolution import StoreProductIdentityResolver
from app.collection.normalization import (
    Availability,
    NormalizedCollectedOffer,
    NormalizedCollectionResult,
    NormalizedInstallmentOption,
    PriceNormalizer,
)
from app.collection.providers import (
    AmazonProvider,
    KabumProvider,
    MagaluProvider,
    PichauProvider,
    TerabyteProvider,
)

__all__ = [
    "CollectionAdapter",
    "BrowserSession",
    "BrowserSettings",
    "CollectionContractError",
    "CollectionError",
    "CollectionNormalizationError",
    "CollectionProvider",
    "CollectionRequest",
    "CollectionResult",
    "InstallmentInterestKind",
    "MarketplacePartyKind",
    "OfferCondition",
    "Availability",
    "NormalizedCollectedOffer",
    "NormalizedCollectionResult",
    "NormalizedInstallmentOption",
    "PriceNormalizer",
    "RawInstallmentOption",
    "PriceHistoryPage",
    "PriceHistoryQueryError",
    "ProductIdentityResolver",
    "DuplicateProviderError",
    "ProviderBlockedError",
    "ProviderCircuitOpenError",
    "ProviderNavigationError",
    "RawCollectedOffer",
    "ResolvedProductIdentity",
    "AmazonProvider",
    "KabumProvider",
    "MagaluProvider",
    "PichauProvider",
    "StoreProductIdentityResolver",
    "TerabyteProvider",
    "UnsupportedSourceError",
    "get_latest_price_observation",
    "list_price_history",
]
