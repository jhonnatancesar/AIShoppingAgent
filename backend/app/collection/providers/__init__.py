from app.collection.providers.stores import (
    AmazonProvider,
    KabumProvider,
    MagaluProvider,
    MercadoLivreProvider,
    PichauProvider,
    TerabyteProvider,
)

V1_PROVIDER_TYPES = (
    PichauProvider,
    TerabyteProvider,
    AmazonProvider,
    KabumProvider,
    MagaluProvider,
    MercadoLivreProvider,
)

__all__ = [
    "AmazonProvider",
    "KabumProvider",
    "MagaluProvider",
    "MercadoLivreProvider",
    "PichauProvider",
    "TerabyteProvider",
    "V1_PROVIDER_TYPES",
]
