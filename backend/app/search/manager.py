"""Search exclusivamente pelo César Core; nunca extrai páginas."""

import logging
from dataclasses import replace
from uuid import uuid4

from app.core.config import Settings
from app.search.cesar_core import CesarCoreSearchProvider
from app.search.contracts import (
    WebSearchError,
    WebSearchProvider,
    WebSearchResponse,
)
from app.search.telemetry import observe_search

LOGGER = logging.getLogger(__name__)


class WebSearchManager:
    def __init__(self, primary: WebSearchProvider) -> None:
        self._primary = primary

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
            LOGGER.info(
                "web_search_rejected",
                extra={"correlation_id": correlation_id, "reason": error.code},
            )
            raise
        response = replace(response, results=response.results[:limit])
        observe_search(response)
        return response


def build_web_search_manager(settings: Settings) -> WebSearchManager:
    if settings.cesar_core_api_key_file is None:
        raise WebSearchError("core_search_credential_unavailable")
    primary = CesarCoreSearchProvider(
        api_key_file=settings.cesar_core_api_key_file,
        base_url=settings.cesar_core_base_url,
        service=settings.cesar_core_service,
        service_class=settings.cesar_core_service_class,
        timeout_seconds=settings.cesar_core_search_timeout_seconds,
    )
    return WebSearchManager(primary)
