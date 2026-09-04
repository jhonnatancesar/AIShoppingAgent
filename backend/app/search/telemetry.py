"""Metadados de Search e enriquecimento, sem query/URL/conteúdo/credenciais."""

import logging

from prometheus_client import Counter

from app.observability.metrics import METRICS_REGISTRY

LOGGER = logging.getLogger(__name__)
SEARCH = Counter(
    "aishopping_web_search_total",
    "Buscas concluídas",
    ("provider", "fallback", "cached"),
    registry=METRICS_REGISTRY,
)
QUERIES = Counter(
    "aishopping_web_search_queries_total",
    "Queries reportadas",
    ("provider",),
    registry=METRICS_REGISTRY,
)
CREDITS = Counter(
    "aishopping_firecrawl_credits_total",
    "Créditos reportados",
    ("operation",),
    registry=METRICS_REGISTRY,
)
SCRAPES = Counter(
    "aishopping_scrape_enrichment_total",
    "URLs tentadas para enriquecimento",
    ("outcome",),
    registry=METRICS_REGISTRY,
)


def observe_search(response):
    provider = (
        response.provider
        if response.provider in {"cesar_core", "firecrawl"}
        else "other"
    )
    SEARCH.labels(
        provider,
        str(response.fallback_reason is not None).lower(),
        str(response.cached).lower(),
    ).inc()
    QUERIES.labels(provider).inc(0 if response.cached else response.queries_used)
    if response.credits_used is not None:
        CREDITS.labels("search").inc(response.credits_used)
    LOGGER.info(
        "web_search_completed",
        extra={
            "search_provider": provider,
            "source": response.source,
            "search_fallback": "firecrawl" if response.fallback_reason else None,
            "fallback_reason": response.fallback_reason,
            "cached": response.cached,
            "queries_used": response.queries_used,
            "request_id": response.request_id,
            "correlation_id": response.correlation_id,
            "upstream_request_id": response.upstream_request_id,
            "credits_used": response.credits_used,
        },
    )


def observe_scrape(*, outcome: str, credits=None, correlation_id=None):
    SCRAPES.labels(outcome).inc()
    if credits is not None:
        CREDITS.labels("scrape").inc(credits)
    LOGGER.info(
        "scrape_enrichment",
        extra={
            "scrape_enrichment": "firecrawl",
            "urls_scraped": 1,
            "outcome": outcome,
            "credits_used": credits,
            "correlation_id": correlation_id,
        },
    )
