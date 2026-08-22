from app.collection.providers.stores import (
    AmazonProvider,
    KabumProvider,
    MagaluProvider,
    PichauProvider,
    TerabyteProvider,
)

V1_PROVIDER_TYPES = (
    PichauProvider,
    TerabyteProvider,
    AmazonProvider,
    KabumProvider,
    MagaluProvider,
)

__all__ = [
    "AmazonProvider",
    "KabumProvider",
    "MagaluProvider",
    "PichauProvider",
    "TerabyteProvider",
    "V1_PROVIDER_TYPES",
]
