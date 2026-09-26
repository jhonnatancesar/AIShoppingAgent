"""Despacho de coletas sem conhecimento de navegador ou marketplace."""

import asyncio
import logging
from collections.abc import Iterable

from app.collection.contracts import (
    CollectionProvider,
    CollectionRequest,
    CollectionResult,
    ProductPageRead,
    ProductPageReadStatus,
    RawCollectedOffer,
)
from app.collection.errors import (
    CollectionContractError,
    DuplicateProviderError,
    UnsupportedSourceError,
)
from app.core.urls import normalize_http_url

logger = logging.getLogger("app.collection.adapter")


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

    async def enrich_marketplace_parties(
        self, source_code: str, offers: tuple[RawCollectedOffer, ...]
    ) -> tuple[RawCollectedOffer, ...]:
        """Enriquece apenas quando o provider oferece a extensão opcional."""
        provider = self._providers.get(source_code)
        if provider is None:
            raise UnsupportedSourceError(
                f"no provider registered for source: {source_code}"
            )
        enrich = getattr(provider, "enrich_marketplace_parties", None)
        if enrich is None:
            return offers
        enriched = await enrich(offers)
        if len(enriched) != len(offers):
            raise CollectionContractError(
                "marketplace enrichment must preserve offer count and order"
            )
        return enriched

    async def enrich_installment_options(
        self, source_code: str, offers: tuple[RawCollectedOffer, ...]
    ) -> tuple[RawCollectedOffer, ...]:
        """Enriquece apenas quando o provider oferece a extensão opcional
        (TASK-089): Pichau/Terabyte a implementam de verdade; Amazon/KaBuM!
        mantêm o padrão (nenhuma navegação extra, nenhuma tabela existe)."""
        provider = self._providers.get(source_code)
        if provider is None:
            raise UnsupportedSourceError(
                f"no provider registered for source: {source_code}"
            )
        enrich = getattr(provider, "enrich_installment_options", None)
        if enrich is None:
            return offers
        enriched = await enrich(offers)
        if len(enriched) != len(offers):
            raise CollectionContractError(
                "installment enrichment must preserve offer count and order"
            )
        return enriched

    async def enrich_offer_details(
        self, source_code: str, offers: tuple[RawCollectedOffer, ...]
    ) -> tuple[RawCollectedOffer, ...]:
        """Executa todas as capabilities de detalhe numa única navegação."""
        provider = self._providers.get(source_code)
        if provider is None:
            raise UnsupportedSourceError(
                f"no provider registered for source: {source_code}"
            )
        enrich = getattr(provider, "enrich_offer_details", None)
        if enrich is None:
            return offers
        enriched = await enrich(offers)
        if len(enriched) != len(offers):
            raise CollectionContractError(
                "detail enrichment must preserve offer count and order"
            )
        return enriched

    async def read_product_page(self, source_code: str, url: str) -> ProductPageRead:
        """TASK-128 (etapa 2): lê a página de um produto pelo provider da
        loja, quando ele oferece a extensão opcional. Fonte sem provider
        ou sem a extensão = `UNSUPPORTED`; qualquer erro da navegação vira
        `FAILED` (passageiro) -- nunca derruba o ciclo do worker."""
        provider = self._providers.get(source_code)
        read = getattr(provider, "read_product_page", None)
        if read is None:
            return ProductPageRead(ProductPageReadStatus.UNSUPPORTED)
        if normalize_http_url(url) is None:
            return ProductPageRead(ProductPageReadStatus.FAILED)
        try:
            result = await read(url)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "product_page_read_failed",
                extra={"source_code": source_code, "error": type(error).__name__},
            )
            return ProductPageRead(ProductPageReadStatus.FAILED)
        if not isinstance(result, ProductPageRead):
            raise CollectionContractError(
                "product page read must return a ProductPageRead"
            )
        return result

    async def collect_selected(
        self, requests: Iterable[CollectionRequest]
    ) -> tuple[CollectionResult, ...]:
        """Executa exatamente as fontes escolhidas, em paralelo e na mesma ordem."""
        selected = tuple(requests)
        if len({request.source_code for request in selected}) != len(selected):
            raise CollectionContractError("selected sources must not be duplicated")
        return tuple(await asyncio.gather(*(self.collect(item) for item in selected)))
