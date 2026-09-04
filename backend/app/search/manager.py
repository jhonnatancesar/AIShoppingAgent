"""Seleção de Search e fallback explícito; nunca faz extração de páginas."""

import logging
from dataclasses import replace
from uuid import uuid4

from app.core.config import Settings
from app.search.cesar_core import CesarCoreSearchProvider
from app.search.contracts import (
    WebSearchError,
    WebSearchProvider,
    WebSearchResponse,
    WebSearchResult,
)
from app.search.firecrawl import FirecrawlSearchError, FirecrawlSearchProvider
from app.search.telemetry import observe_search

LOGGER = logging.getLogger(__name__)


class FirecrawlSearchAdapter:
    def __init__(self, client: FirecrawlSearchProvider) -> None:
        self._client = client

    async def search(
        self, query: str, *, limit: int, correlation_id: str
    ) -> WebSearchResponse:
        try:
            response = await self._client.search(query, sources=("web",), limit=limit)
        except FirecrawlSearchError as error:
            raise WebSearchError("firecrawl_search_failed") from error
        return WebSearchResponse(
            tuple(
                WebSearchResult(r.title, r.url, r.description or "", i + 1)
                for i, r in enumerate(response.results[:limit])
            ),
            "firecrawl",
            "firecrawl",
            correlation_id,
            response.request_id,
            credits_used=response.credits_used,
        )


class WebSearchManager:
    def __init__(
        self, primary: WebSearchProvider, fallback: WebSearchProvider | None = None
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    async def search(
        self, query: str, *, limit: int = 6, correlation_id: str | None = None
    ) -> WebSearchResponse:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query.strip()) > 500
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise WebSearchError("search_invalid_input")
        correlation_id = correlation_id or str(uuid4())
        try:
            response = await self._primary.search(
                query, limit=limit, correlation_id=correlation_id
            )
        except WebSearchError as error:
            if not error.retryable or self._fallback is None:
                LOGGER.info(
                    "web_search_rejected",
                    extra={"correlation_id": correlation_id, "reason": error.code},
                )
                raise
            LOGGER.info(
                "web_search_fallback",
                extra={
                    "correlation_id": correlation_id,
                    "search_fallback": "firecrawl",
                    "fallback_reason": error.code,
                },
            )
            response = await self._fallback.search(
                query, limit=limit, correlation_id=correlation_id
            )
            response = replace(response, fallback_reason=error.code)
        response = replace(response, results=response.results[:limit])
        observe_search(response)
        return response


def build_web_search_manager(
    settings: Settings, firecrawl: FirecrawlSearchProvider | None
) -> WebSearchManager:
    fallback = FirecrawlSearchAdapter(firecrawl) if firecrawl is not None else None
    if not settings.cesar_core_search_enabled:
        if fallback is None:
            raise WebSearchError("search_not_configured")
        return WebSearchManager(
            fallback
        )  # rollback explícito, sem fingir passagem pelo Core
    if settings.cesar_core_api_key_file is None:
        raise WebSearchError("core_search_credential_unavailable")
    primary = CesarCoreSearchProvider(
        api_key_file=settings.cesar_core_api_key_file,
        base_url=settings.cesar_core_base_url,
        service=settings.cesar_core_service,
        service_class=settings.cesar_core_service_class,
        timeout_seconds=settings.cesar_core_search_timeout_seconds,
    )
    return WebSearchManager(
        primary, fallback if settings.cesar_core_search_fallback_enabled else None
    )
