from app.collection.providers.stores import (
    AmazonProvider,
    KabumProvider,
    PichauProvider,
    TerabyteProvider,
)

V1_PROVIDER_TYPES = (PichauProvider, TerabyteProvider, AmazonProvider, KabumProvider)

__all__ = [
    "AmazonProvider",
    "KabumProvider",
    "PichauProvider",
    "TerabyteProvider",
    "V1_PROVIDER_TYPES",
]
