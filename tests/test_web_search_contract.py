"""Contract opt-in real: Core isolado pelo harness, SearXNG e Firecrawl reais."""

import asyncio
import json
import os
from dataclasses import asdict

import httpx
import pytest
from app.core.config import Settings
from app.core.resilience import RetryPolicy
from app.search.contracts import WebSearchError
from app.search.firecrawl import FirecrawlSearchProvider
from app.search.manager import build_web_search_manager

pytestmark = pytest.mark.skipif(
    os.environ.get("AISHOPPING_RUN_SEARCH_CONTRACT") != "1",
    reason="Exige harness DEV isolado e os serviços reais, nunca PROD",
)


def test_real_search_cache_zero_rejection_and_fallback(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    from cesar_core.api.deps import QUOTA_LIMITER
    from cesar_core.applications.identity import ApplicationId
    from cesar_core.applications.registry import REGISTRY

    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert (
        settings.cesar_core_search_enabled
        and settings.cesar_core_search_fallback_enabled
    )
    assert settings.firecrawl_api_key is not None
    secrets = [
        settings.firecrawl_api_key.get_secret_value(),
        settings.cesar_core_api_key_file.read_text().strip(),
    ]
    upstream_calls = []
    original = httpx.AsyncHTTPTransport.handle_async_request

    async def observe(transport, request):
        if request.url.host == "api.firecrawl.dev":
            upstream_calls.append(request.url.path)
        return await original(transport, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", observe)
    firecrawl = FirecrawlSearchProvider(
        settings.firecrawl_api_key, retry_policy=RetryPolicy(max_attempts=1)
    )
    manager = build_web_search_manager(settings, firecrawl)
    query = "preço e disponibilidade de Ryzen 7 5700X3D no Brasil"
    one = asyncio.run(manager.search(query, limit=3, correlation_id="118g-e2e-first"))
    two = asyncio.run(manager.search(query, limit=3, correlation_id="118g-e2e-second"))
    assert (
        one.provider == two.provider == "cesar_core" and one.source == "searxng-search"
    )
    assert 1 <= len(one.results) <= 3 and all(
        r.title and r.url and r.snippet and r.position for r in one.results
    )
    assert not one.cached and two.cached and one.results == two.results
    assert not upstream_calls

    empty = asyncio.run(
        manager.search(
            '"118g-no-match-73c98baf62894e78a1" site:118g-no-such-domain.invalid',
            limit=3,
        )
    )
    assert empty.results == () and not upstream_calls

    key = tmp_path / "invalid-key"
    key.write_text("synthetic-invalid-core-key")
    denied = build_web_search_manager(
        settings.model_copy(update={"cesar_core_api_key_file": key}), firecrawl
    )
    with pytest.raises(WebSearchError) as error:
        asyncio.run(denied.search(query, limit=3))
    assert not error.value.retryable and not upstream_calls
    entry = REGISTRY[ApplicationId.GG_OFERTA]
    with monkeypatch.context() as patch:
        patch.setitem(
            REGISTRY,
            ApplicationId.GG_OFERTA,
            entry.model_copy(update={"allowed_capabilities": frozenset()}),
        )
        with pytest.raises(WebSearchError):
            asyncio.run(manager.search(query, limit=3))
    assert not upstream_calls
    with monkeypatch.context() as patch:
        QUOTA_LIMITER.reset()
        patch.setenv("CESAR_CORE_SECURITY_SEARCH_REQUESTS_PER_MINUTE", "1")
        asyncio.run(manager.search(query, limit=3))
        with pytest.raises(WebSearchError) as error:
            asyncio.run(manager.search(query, limit=3))
        assert not error.value.retryable
    QUOTA_LIMITER.reset()
    assert not upstream_calls

    # O harness muda o target para uma porta loopback sem listener: falha real de conexão.
    down = build_web_search_manager(
        settings.model_copy(
            update={"cesar_core_base_url": os.environ["AISHOPPING_CORE_REFUSED_URL"]}
        ),
        firecrawl,
    )
    fallback = asyncio.run(down.search(query, limit=3))
    assert (
        fallback.provider == "firecrawl"
        and fallback.fallback_reason == "core_search_network_unavailable"
    )
    assert upstream_calls == ["/v2/search"]
    assert len(fallback.results) <= 3 and fallback.credits_used is not None
    from app.observability.metrics import METRICS_REGISTRY
    from prometheus_client import generate_latest

    metrics = generate_latest(METRICS_REGISTRY).decode()
    trace_records = [r.__dict__ for r in caplog.records if r.msg == "web_search_completed"]
    assert any(r.get("search_provider") == "cesar_core" and r.get("cached") for r in trace_records)
    assert any(r.get("search_fallback") == "firecrawl" and r.get("fallback_reason") for r in trace_records)
    assert 'aishopping_web_search_total' in metrics and 'aishopping_firecrawl_credits_total' in metrics
    assert all(secret not in caplog.text + repr(trace_records) + metrics for secret in secrets)
    print(
        json.dumps(
            {
                "first": asdict(one),
                "second_cached": two.cached,
                "zero_results": len(empty.results),
                "auth_capability_quota_no_fallback": True,
                "fallback": asdict(fallback),
                "firecrawl_calls": upstream_calls,
                "secret_scan": "PASS",
                "trace_events": len(trace_records),
                "metrics": "PASS",
            },
            ensure_ascii=True,
        )
    )
