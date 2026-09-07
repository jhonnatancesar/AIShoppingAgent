import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from app.core.config import Settings
from app.market_research.service import _search_with_enrichment
from app.search.cesar_core import CesarCoreSearchProvider
from app.search.contracts import WebSearchError, WebSearchResponse, WebSearchResult
from app.search.manager import WebSearchManager, build_web_search_manager


class Stub:
    def __init__(self, results=(), error=None):
        self.results, self.error, self.calls = results, error, 0

    async def search(self, query, *, limit, correlation_id):
        self.calls += 1
        if self.error:
            raise self.error
        return WebSearchResponse(
            self.results, "cesar_core", "searxng-search", correlation_id
        )


def response(correlation="corr", count=1):
    return {
        "request_id": "core-id",
        "correlation_id": correlation,
        "provider_gateway": "omniroute",
        "provider": "searxng-search",
        "cached": False,
        "usage": {"queries_used": 1},
        "results": [
            {
                "title": "Ryzen",
                "url": f"https://shop{i}.test/item",
                "snippet": "preço",
                "position": i + 1,
            }
            for i in range(count)
        ],
    }


def core(tmp_path, handler):
    key = tmp_path / "key"
    key.write_text("synthetic-only")
    return CesarCoreSearchProvider(
        api_key_file=key,
        base_url="http://127.0.0.1:8100",
        service="worker",
        client_factory=lambda **kw: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kw
        ),
    )


def test_accepts_host_docker_internal_base_url(tmp_path):
    """DEC-121: mesmo validador compartilhado com AI/Fetch -- coberto aqui
    também para não deixar Search desalinhado se algum dia rodar em
    container (hoje é concern exclusivo do collection_worker nativo)."""
    key = tmp_path / "key"
    key.write_text("synthetic-only", encoding="utf-8")
    provider_instance = CesarCoreSearchProvider(
        api_key_file=key,
        base_url="http://host.docker.internal:8100",
        service="worker",
    )
    assert provider_instance is not None


def test_rejects_arbitrary_hostname_base_url(tmp_path):
    key = tmp_path / "key"
    key.write_text("synthetic-only", encoding="utf-8")
    with pytest.raises(ValueError, match="loopback or host.docker.internal"):
        CesarCoreSearchProvider(
            api_key_file=key,
            base_url="http://cesar-core.example.com:8100",
            service="worker",
        )


def test_payload_normalization_and_final_limit(tmp_path):
    wire = []

    def handler(req):
        wire.append(json.loads(req.content))
        assert req.headers["Authorization"] == "Bearer synthetic-only"
        assert req.headers["X-Purpose"] == "market_research"
        return httpx.Response(200, json=response(count=5))

    result = asyncio.run(
        core(tmp_path, handler).search("Ryzen preço", limit=3, correlation_id="corr")
    )
    assert len(result.results) == 3 and result.provider == "cesar_core"
    assert wire == [
        {
            "query": "Ryzen preço",
            "max_results": 3,
            "requirements": {"service_class": "economy", "cost_policy": "free_only"},
        }
    ]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 429, 502])
def test_no_fallback_for_rejections(tmp_path, status):
    fallback = Stub()
    manager = WebSearchManager(core(tmp_path, lambda r: httpx.Response(status)))
    with pytest.raises(WebSearchError):
        asyncio.run(manager.search("query"))
    assert fallback.calls == 0


@pytest.mark.parametrize("status", [500, 503, 504])
def test_unavailability_is_fail_closed_without_firecrawl(tmp_path, status):
    fallback = Stub()
    with pytest.raises(WebSearchError):
        asyncio.run(
            WebSearchManager(core(tmp_path, lambda r: httpx.Response(status))).search(
                "query"
            )
        )
    assert fallback.calls == 0


@pytest.mark.parametrize(
    "code", ["quota_store_unavailable", "quota_store_misconfigured"]
)
def test_no_fallback_for_quota_store_failure(tmp_path, code):
    # ADR 0018 (César Core): 503 do próprio gate de quota (Redis fora ou
    # configuração incompatível) nunca deve virar fallback Firecrawl --
    # mascararia uma falha real da proteção de quota, não indisponibilidade
    # legítima do provider upstream.
    fallback = Stub()
    manager = WebSearchManager(
        core(
            tmp_path,
            lambda r: httpx.Response(503, json={"error": {"code": code}}),
        )
    )
    with pytest.raises(WebSearchError) as error:
        asyncio.run(manager.search("query"))
    assert not error.value.retryable and error.value.code == "core_search_rejected"
    assert fallback.calls == 0


def test_zero_is_success_without_fallback():
    fallback = Stub()
    assert asyncio.run(WebSearchManager(Stub()).search("query")).results == ()
    assert fallback.calls == 0


def test_core_configuration_is_mandatory(tmp_path):
    settings = Settings(_env_file=None)
    with pytest.raises(WebSearchError, match="core_search_credential_unavailable"):
        build_web_search_manager(settings)
    manager = build_web_search_manager(
        settings.model_copy(update={"cesar_core_api_key_file": tmp_path / "key"})
    )
    assert isinstance(manager._primary, CesarCoreSearchProvider)


@pytest.mark.parametrize(
    "sufficient,empty", [(True, False), (False, False), (False, True)]
)
def test_search_and_enrichment_are_separate(monkeypatch, sufficient, empty):
    monkeypatch.setattr(
        "app.market_research.service._evidence_matches_identity",
        lambda *a, **kw: sufficient or kw.get("description") == "extracted",
    )
    results = (
        ()
        if empty
        else tuple(
            WebSearchResult("Ryzen", f"https://shop{i}.test/item", "weak", i + 1)
            for i in range(6)
        )
    )
    primary, fallback = Stub(results), Stub()

    class Scraper:
        calls = 0

        async def scrape_basic(self, url):
            self.calls += 1
            return SimpleNamespace(title="Ryzen", markdown="extracted", credits_used=1)

    scraper = Scraper()
    asyncio.run(
        _search_with_enrichment(
            WebSearchManager(primary),
            enrichment=scraper,
            query="Ryzen",
            product=object(),
            min_items=2,
        )
    )
    assert fallback.calls == 0
    assert scraper.calls == (0 if sufficient or empty else 3)
