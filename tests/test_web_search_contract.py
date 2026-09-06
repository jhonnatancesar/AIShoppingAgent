"""Contract opt-in real: Search exclusivamente por Core e SearXNG."""

import asyncio
import json
import os
from dataclasses import asdict

import httpx
import pytest
from app.core.config import Settings
from app.search.contracts import WebSearchError
from app.search.manager import build_web_search_manager

pytestmark = pytest.mark.skipif(
    os.environ.get("AISHOPPING_RUN_SEARCH_CONTRACT") != "1",
    reason="Exige harness DEV isolado e os serviços reais, nunca PROD",
)


def test_real_search_cache_zero_rejection_and_fail_closed(
    monkeypatch, tmp_path, caplog
):
    caplog.set_level("INFO")
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    secrets = [
        settings.cesar_core_api_key_file.read_text().strip(),
    ]
    upstream_calls = []
    original = httpx.AsyncHTTPTransport.handle_async_request

    async def observe(transport, request):
        if request.url.host == "api.firecrawl.dev":
            upstream_calls.append(request.url.path)
        return await original(transport, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", observe)
    manager = build_web_search_manager(settings)
    query = "Python programming language official documentation"
    one = asyncio.run(manager.search(query, limit=3, correlation_id="118g-e2e-first"))
    two = asyncio.run(manager.search(query, limit=3, correlation_id="118g-e2e-second"))
    assert (
        one.provider == two.provider == "cesar_core" and one.source == "searxng-search"
    )
    assert 1 <= len(one.results) <= 3 and all(
        r.title and r.url and r.snippet and r.position for r in one.results
    )
    assert two.cached and one.results == two.results
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
        settings.model_copy(update={"cesar_core_api_key_file": key})
    )
    with pytest.raises(WebSearchError) as error:
        asyncio.run(denied.search(query, limit=3))
    assert not error.value.retryable and not upstream_calls
    # O harness muda o target para uma porta loopback sem listener: falha real de conexão.
    down = build_web_search_manager(
        settings.model_copy(
            update={
                "cesar_core_base_url": os.environ.get(
                    "AISHOPPING_CORE_REFUSED_URL", "http://127.0.0.1:65534"
                )
            }
        )
    )
    with pytest.raises(WebSearchError) as unavailable:
        asyncio.run(down.search(query, limit=3))
    assert unavailable.value.code == "core_search_network_unavailable"
    assert not upstream_calls
    from app.observability.metrics import METRICS_REGISTRY
    from prometheus_client import generate_latest

    metrics = generate_latest(METRICS_REGISTRY).decode()
    trace_records = [
        r.__dict__ for r in caplog.records if r.msg == "web_search_completed"
    ]
    assert any(
        r.get("search_provider") == "cesar_core" and r.get("cached")
        for r in trace_records
    )
    assert not any(r.get("search_fallback") for r in trace_records)
    assert "aishopping_web_search_total" in metrics
    assert all(
        secret not in caplog.text + repr(trace_records) + metrics for secret in secrets
    )
    print(
        json.dumps(
            {
                "first": asdict(one),
                "second_cached": two.cached,
                "zero_results": len(empty.results),
                "auth_capability_quota_no_fallback": True,
                "firecrawl_calls": upstream_calls,
                "secret_scan": "PASS",
                "trace_events": len(trace_records),
                "metrics": "PASS",
            },
            ensure_ascii=True,
        )
    )
