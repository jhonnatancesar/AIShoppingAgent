"""Contratos para coleta de ofertas em fontes selecionadas."""

from app.collection.adapter import CollectionAdapter
from app.collection.browser import BrowserSession, BrowserSettings
from app.collection.contracts import (
    CollectionProvider,
    CollectionRequest,
    CollectionResult,
    RawCollectedOffer,
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
from app.collection.normalization import (
    Availability,
    NormalizedCollectedOffer,
    NormalizedCollectionResult,
    PriceNormalizer,
)
from app.collection.providers import (
    AmazonProvider,
    KabumProvider,
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
    "Availability",
    "NormalizedCollectedOffer",
    "NormalizedCollectionResult",
    "PriceNormalizer",
    "PriceHistoryPage",
    "PriceHistoryQueryError",
    "DuplicateProviderError",
    "ProviderBlockedError",
    "ProviderCircuitOpenError",
    "ProviderNavigationError",
    "RawCollectedOffer",
    "AmazonProvider",
    "KabumProvider",
    "PichauProvider",
    "TerabyteProvider",
    "UnsupportedSourceError",
    "get_latest_price_observation",
    "list_price_history",
]
