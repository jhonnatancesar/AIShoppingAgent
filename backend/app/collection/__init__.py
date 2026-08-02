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
    DuplicateProviderError,
    UnsupportedSourceError,
)

__all__ = [
    "CollectionAdapter",
    "BrowserSession",
    "BrowserSettings",
    "CollectionContractError",
    "CollectionError",
    "CollectionProvider",
    "CollectionRequest",
    "CollectionResult",
    "DuplicateProviderError",
    "RawCollectedOffer",
    "UnsupportedSourceError",
]
