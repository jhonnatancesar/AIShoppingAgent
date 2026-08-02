"""Despacho de coletas sem conhecimento de navegador ou marketplace."""

from collections.abc import Iterable

from app.collection.contracts import (
    CollectionProvider,
    CollectionRequest,
    CollectionResult,
)
from app.collection.errors import (
    CollectionContractError,
    DuplicateProviderError,
    UnsupportedSourceError,
)


class CollectionAdapter:
    """Registro mínimo que encaminha cada pedido ao provider de sua fonte."""

    def __init__(self, providers: Iterable[CollectionProvider] = ()) -> None:
        self._providers: dict[str, CollectionProvider] = {}
        for provider in providers:
            self.register(provider)

    @property
    def supported_sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def register(self, provider: CollectionProvider) -> None:
        source_code = provider.source_code
        if not source_code.strip() or source_code != source_code.strip().lower():
            raise CollectionContractError(
                "provider source_code must be non-blank, trimmed and lowercase"
            )
        if source_code in self._providers:
            raise DuplicateProviderError(
                f"provider already registered for source: {source_code}"
            )
        self._providers[source_code] = provider

    async def collect(self, request: CollectionRequest) -> CollectionResult:
        provider = self._providers.get(request.source_code)
        if provider is None:
            raise UnsupportedSourceError(
                f"no provider registered for source: {request.source_code}"
            )

        result = await provider.collect(request)
        if result.source_code != request.source_code:
            raise CollectionContractError(
                "provider result source does not match requested source"
            )
        if result.started_at < request.requested_at:
            raise CollectionContractError(
                "provider result started before the collection request"
            )
        return result
