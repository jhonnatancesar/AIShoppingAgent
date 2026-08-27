"""Ferramentas de pesquisa externa, separadas dos provedores de LLM."""

from app.search.firecrawl import (
    FirecrawlScrapeResult,
    FirecrawlSearchError,
    FirecrawlSearchProvider,
    FirecrawlSearchResponse,
    FirecrawlSearchResult,
    parse_firecrawl_scrape_response,
    parse_firecrawl_search_response,
)

__all__ = [
    "FirecrawlSearchProvider",
    "FirecrawlSearchError",
    "FirecrawlSearchResponse",
    "FirecrawlSearchResult",
    "FirecrawlScrapeResult",
    "parse_firecrawl_search_response",
    "parse_firecrawl_scrape_response",
]
