"""Contratos para coleta de ofertas em fontes selecionadas."""

from app.collection.adapter import CollectionAdapter
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
    "CollectionContractError",
    "CollectionError",
    "CollectionProvider",
    "CollectionRequest",
    "CollectionResult",
    "DuplicateProviderError",
    "RawCollectedOffer",
    "UnsupportedSourceError",
]
