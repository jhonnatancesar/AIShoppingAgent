"""Ferramentas de pesquisa externa, separadas dos provedores de LLM."""

from app.search.firecrawl import (
    FirecrawlSearchError,
    FirecrawlSearchProvider,
    FirecrawlSearchResponse,
    FirecrawlSearchResult,
    parse_firecrawl_search_response,
)

__all__ = [
    "FirecrawlSearchProvider",
    "FirecrawlSearchError",
    "FirecrawlSearchResponse",
    "FirecrawlSearchResult",
    "parse_firecrawl_search_response",
]
