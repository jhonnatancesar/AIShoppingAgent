import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.collection import (
    AmazonProvider,
    BrowserSession,
    CollectionRequest,
    KabumProvider,
    PichauProvider,
    ProviderBlockedError,
    ProviderNavigationError,
    RawCollectedOffer,
    TerabyteProvider,
)
from app.collection.providers.base import PlaywrightStoreProvider
from app.core.resilience import RetryPolicy
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from scripts.validate_store_providers import should_use_headed

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)

CASES = (
    (
        PichauProvider,
        '<a data-cy="list-product" href="https://www.pichau.com.br/gpu-x"><h2>GPU Pichau</h2><div class="price_vista">R$ 1.999,90</div></a>',
        "gpu-x",
    ),
    (
        TerabyteProvider,
        '<div class="product-item"><a class="product-item__name" href="https://www.terabyteshop.com.br/produto/123/gpu">GPU Tera</a><div class="product-item__new-price"><span>R$ 2.099,90</span></div></div>',
        "123",
    ),
    (
        AmazonProvider,
        '<div data-component-type="s-search-result" data-asin="B0123"><h2><a href="https://www.amazon.com.br/dp/B0123">GPU Amazon</a></h2><span class="a-price"><span class="a-offscreen">R$ 2.199,90</span></span><span>Prime e Entrega GRÁTIS</span><button>Adicionar ao carrinho</button></div>',
        "B0123",
    ),
    (
        KabumProvider,
        '<main><a href="https://www.kabum.com.br/produto/456/gpu"><span class="line-clamp-2">GPU Kabum</span><span class="text-base font-semibold">R$</span><span class="text-base font-semibold">2.299,90</span><span>Vendido por Kabum</span></a></main>',
        "456",
    ),
)


@pytest.mark.parametrize(("provider_type", "html", "external_id"), CASES)
def test_extracts_sanitized_store_card(provider_type, html, external_id) -> None:
    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await provider_type().extract(page, NOW)

    offers = asyncio.run(scenario())

    assert len(offers) == 1
    assert offers[0].external_id == external_id
    assert offers[0].raw_price.startswith("R$")
    assert offers[0].raw_currency == "BRL"
    assert offers[0].evidence["card_text"]


def test_amazon_uses_only_explicit_shipping_and_availability_evidence() -> None:
    html = (
        '<div data-component-type="s-search-result" data-asin="B0E2E">'
        '<h2><a href="https://www.amazon.com.br/dp/B0E2E">Produto</a></h2>'
        '<span class="a-price"><span class="a-offscreen">R$ 99,90</span></span>'
        "<span>Entrega GRÁTIS</span><button>Adicionar ao carrinho</button></div>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await AmazonProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]

    assert offer.raw_shipping == "Frete grátis"
    assert offer.raw_availability == "Disponível"


def test_amazon_does_not_flatten_login_or_first_order_shipping_condition() -> None:
    html = (
        '<div data-component-type="s-search-result" data-asin="B0COND">'
        '<h2><a href="https://www.amazon.com.br/dp/B0COND">Produto</a></h2>'
        '<span class="a-price"><span class="a-offscreen">R$ 99,90</span></span>'
        "<span>Entrega GRÁTIS no seu primeiro pedido</span>"
        "<button>Adicionar ao carrinho</button></div>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await AmazonProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]

    assert offer.raw_shipping is None
    assert offer.raw_availability == "Disponível"


def test_pichau_card_out_of_stock_class_is_unavailable() -> None:
    html = (
        '<a data-cy="list-product" href="https://www.pichau.com.br/memoria-esgotada">'
        "<h2>Memoria Kingston Fury Beast</h2>"
        '<p class="mui-8rpawh-out_of_stock">Esgotado</p></a>'
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await PichauProvider().extract(page, NOW)

    offers = asyncio.run(scenario())

    assert offers == ()  # sem preço numérico: não vira candidato desta execução


def test_pichau_card_availability_badge_is_available() -> None:
    html = (
        '<a data-cy="list-product" href="https://www.pichau.com.br/gpu-disponivel">'
        "<h2>GPU Pichau</h2>"
        '<div class="mui-c0se7r-availability_span-availability_span_available">50 '
        "<small>UNID</small></div>"
        '<div class="price_vista">R$ 1.999,90</div></a>'
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await PichauProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]

    assert offer.raw_availability == "Disponível"


def test_pichau_card_without_badge_or_out_of_stock_is_unknown() -> None:
    html = (
        '<a data-cy="list-product" href="https://www.pichau.com.br/gpu-neutra">'
        "<h2>GPU Pichau</h2>"
        '<div class="price_vista">R$ 1.999,90</div></a>'
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await PichauProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]

    assert offer.raw_availability is None


def test_terabyte_card_estoque_zero_is_unavailable() -> None:
    html = (
        '<div class="product-item" data-tss-estoque="0">'
        '<a class="product-item__name" '
        'href="https://www.terabyteshop.com.br/produto/321/memoria">'
        "Memoria Esgotada</a>"
        '<div class="product-item__new-price">Indisponível</div></div>'
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await TerabyteProvider().extract(page, NOW)

    offers = asyncio.run(scenario())

    assert offers == ()  # sem preço numérico: não vira candidato desta execução


def test_terabyte_card_estoque_one_is_available() -> None:
    html = (
        '<div class="product-item" data-tss-estoque="1">'
        '<a class="product-item__name" '
        'href="https://www.terabyteshop.com.br/produto/123/gpu">GPU Tera</a>'
        '<div class="product-item__new-price"><span>R$ 2.099,90</span></div></div>'
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await TerabyteProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]

    assert offer.raw_availability == "Disponível"


def test_kabum_card_restam_unid_is_available() -> None:
    html = (
        '<main><a href="https://www.kabum.com.br/produto/456/gpu">'
        '<span class="line-clamp-2">GPU Kabum</span>'
        '<span class="text-base font-semibold">R$</span>'
        '<span class="text-base font-semibold">2.299,90</span>'
        "Restam 9 Unid.</a></main>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await KabumProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]

    assert offer.raw_availability == "Disponível"


def test_kabum_card_without_restam_is_unknown() -> None:
    html = (
        '<main><a href="https://www.kabum.com.br/produto/789/gpu">'
        '<span class="line-clamp-2">GPU Kabum</span>'
        '<span class="text-base font-semibold">R$</span>'
        '<span class="text-base font-semibold">2.299,90</span></a></main>'
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await KabumProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]

    assert offer.raw_availability is None


def test_offers_from_rows_skips_cards_without_numeric_price() -> None:
    provider = KabumProvider()
    rows = [
        {"title": "Sem preço", "url": "https://x/a", "price": None},
        {"title": "Preço textual", "url": "https://x/b", "price": "Indisponível"},
        {"title": "Preço válido", "url": "https://x/c", "price": "R$ 10,00"},
    ]

    offers = provider.offers_from_rows(rows, NOW)

    assert [offer.url for offer in offers] == ["https://x/c"]


def test_fallback_resolves_only_top_k_unknown_by_price_ascending(monkeypatch) -> None:
    goto_calls: list[str] = []

    class Response:
        status = 200

    class First:
        async def wait_for(self, **kwargs):
            return None

    class Locator:
        first = First()

    offers = (
        RawCollectedOffer(
            source_code="fk",
            url="https://x/a",
            title="A",
            collected_at=NOW,
            raw_price="R$ 100,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
        RawCollectedOffer(
            source_code="fk",
            url="https://x/b",
            title="B",
            collected_at=NOW,
            raw_price="R$ 50,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
        RawCollectedOffer(
            source_code="fk",
            url="https://x/c",
            title="C",
            collected_at=NOW,
            raw_price="R$ 10,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
        RawCollectedOffer(
            source_code="fk",
            url="https://x/d",
            title="D",
            collected_at=NOW,
            raw_price="R$ 5,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
        RawCollectedOffer(
            source_code="fk",
            url="https://x/already",
            title="E",
            collected_at=NOW,
            raw_price="R$ 1,00",
            raw_currency="BRL",
            raw_availability="Disponível",
        ),
    )

    class Page:
        async def goto(self, url, **kwargs):
            goto_calls.append(url)
            return Response()

        def locator(self, selector):
            return Locator()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    class FallbackProvider(PlaywrightStoreProvider):
        source_code = "fk"
        result_selector = ".offer"

        def build_url(self, query):
            return "https://example.test/search"

        async def extract(self, page, collected_at):
            return offers

        async def resolve_product_availability(self, page):
            return None if goto_calls[-1].endswith("/d") else "Disponível"

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "fk", "GPU", NOW)
    provider = FallbackProvider(
        clock=lambda: NOW, availability_fallback_max_candidates=3
    )

    result = asyncio.run(provider.collect(request))

    # primeira navegação é a busca; depois, top 3 mais baratos entre os
    # UNKNOWN, em ordem crescente de amount
    assert goto_calls == [
        "https://example.test/search",
        "https://x/d",
        "https://x/c",
        "https://x/b",
    ]
    resolved = {offer.url: offer.raw_availability for offer in result.offers}
    assert resolved["https://x/d"] is None  # ambíguo continua UNKNOWN
    assert resolved["https://x/c"] == "Disponível"
    assert resolved["https://x/b"] == "Disponível"
    assert resolved["https://x/a"] is None  # além do top-3, não abriu
    assert resolved["https://x/already"] == "Disponível"  # já resolvido no card


def test_fallback_isolates_failure_and_still_resolves_next_candidate(
    monkeypatch,
) -> None:
    goto_calls: list[str] = []

    class Response:
        status = 200

    class First:
        async def wait_for(self, **kwargs):
            return None

    class Locator:
        first = First()

    offers = (
        RawCollectedOffer(
            source_code="fk2",
            url="https://x/broken",
            title="B",
            collected_at=NOW,
            raw_price="R$ 10,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
        RawCollectedOffer(
            source_code="fk2",
            url="https://x/ok",
            title="O",
            collected_at=NOW,
            raw_price="R$ 20,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
    )

    class Page:
        async def goto(self, url, **kwargs):
            goto_calls.append(url)
            if url.endswith("/broken"):
                raise RuntimeError("navigation failed")
            return Response()

        def locator(self, selector):
            return Locator()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    class FlakyFallbackProvider(PlaywrightStoreProvider):
        source_code = "fk2"
        result_selector = ".offer"

        def build_url(self, query):
            return "https://example.test/search"

        async def extract(self, page, collected_at):
            return offers

        async def resolve_product_availability(self, page):
            return "Disponível"

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "fk2", "GPU", NOW)
    provider = FlakyFallbackProvider(clock=lambda: NOW)

    result = asyncio.run(provider.collect(request))

    assert goto_calls == [
        "https://example.test/search",
        "https://x/broken",
        "https://x/ok",
    ]
    resolved = {offer.url: offer.raw_availability for offer in result.offers}
    assert resolved["https://x/broken"] is None
    assert resolved["https://x/ok"] == "Disponível"


def test_fallback_stops_entire_cycle_on_blocked_status(monkeypatch) -> None:
    goto_calls: list[str] = []

    class Response:
        def __init__(self, status):
            self.status = status

    class First:
        async def wait_for(self, **kwargs):
            return None

    class Locator:
        first = First()

    offers = (
        RawCollectedOffer(
            source_code="fk3",
            url="https://x/cheapest",
            title="A",
            collected_at=NOW,
            raw_price="R$ 5,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
        RawCollectedOffer(
            source_code="fk3",
            url="https://x/second",
            title="B",
            collected_at=NOW,
            raw_price="R$ 10,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
        RawCollectedOffer(
            source_code="fk3",
            url="https://x/third",
            title="C",
            collected_at=NOW,
            raw_price="R$ 15,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
    )

    class Page:
        async def goto(self, url, **kwargs):
            goto_calls.append(url)
            if url.endswith("/cheapest"):
                return Response(403)
            return Response(200)

        def locator(self, selector):
            return Locator()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    class BlockedFallbackProvider(PlaywrightStoreProvider):
        source_code = "fk3"
        result_selector = ".offer"

        def build_url(self, query):
            return "https://example.test/search"

        async def extract(self, page, collected_at):
            return offers

        async def resolve_product_availability(self, page):
            return "Disponível"

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "fk3", "GPU", NOW)
    provider = BlockedFallbackProvider(
        clock=lambda: NOW, availability_fallback_max_candidates=3
    )

    result = asyncio.run(provider.collect(request))

    # 403 no primeiro candidato (o mais barato) encerra o ciclo: os outros
    # dois candidatos nunca são abertos, mesmo estando dentro do top-K.
    assert goto_calls == ["https://example.test/search", "https://x/cheapest"]
    resolved = {offer.url: offer.raw_availability for offer in result.offers}
    assert resolved["https://x/cheapest"] is None
    assert resolved["https://x/second"] is None
    assert resolved["https://x/third"] is None


def test_fallback_skips_navigation_when_provider_has_no_product_page_hook(
    monkeypatch,
) -> None:
    goto_calls: list[str] = []

    class Response:
        status = 200

    class First:
        async def wait_for(self, **kwargs):
            return None

    class Locator:
        first = First()

    offers = (
        RawCollectedOffer(
            source_code="amazon",
            url="https://x/z",
            title="Z",
            collected_at=NOW,
            raw_price="R$ 10,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
    )

    class Page:
        async def goto(self, url, **kwargs):
            goto_calls.append(url)
            return Response()

        def locator(self, selector):
            return Locator()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "amazon", "GPU", NOW)
    provider = AmazonProvider(clock=lambda: NOW)
    monkeypatch.setattr(
        provider, "extract", lambda page, collected_at: _async_offers(offers)
    )

    result = asyncio.run(provider.collect(request))

    assert len(goto_calls) == 1  # só a navegação de busca, sem fallback
    assert result.offers == offers


async def _async_offers(offers):
    return offers


def test_builds_encoded_source_urls() -> None:
    assert "RTX+5070" in AmazonProvider().build_url("RTX 5070")
    assert "RTX+5070" in PichauProvider().build_url("RTX 5070")
    assert "RTX+5070" in TerabyteProvider().build_url("RTX 5070")
    assert "RTX-5070" in KabumProvider().build_url("RTX 5070")


def test_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        KabumProvider(max_offers=0)


def test_uses_headed_only_for_protected_sources_by_default() -> None:
    assert should_use_headed("pichau") is True
    assert should_use_headed("terabyte") is True
    assert should_use_headed("amazon") is False
    assert should_use_headed("kabum") is False
    assert should_use_headed("pichau", force_headless=True) is False
    assert should_use_headed("amazon", force_headed=True) is True
    with pytest.raises(ValueError):
        should_use_headed("amazon", force_headed=True, force_headless=True)


def test_provider_rejects_silent_empty_collection(monkeypatch) -> None:
    class Response:
        status = 200

    class First:
        async def wait_for(self, **kwargs):
            return None

    class Locator:
        first = First()

    class Page:
        async def goto(self, *args, **kwargs):
            return Response()

        def locator(self, selector):
            return Locator()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    class EmptyProvider(PlaywrightStoreProvider):
        source_code = "empty"
        result_selector = ".offer"

        def build_url(self, query):
            return "https://example.test/search"

        async def extract(self, page, collected_at):
            return ()

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "empty", "GPU", NOW)

    with pytest.raises(ProviderBlockedError):
        asyncio.run(EmptyProvider(clock=lambda: NOW).collect(request))


def test_provider_retries_safe_navigation_timeout_only(monkeypatch) -> None:
    calls = 0

    class Page:
        async def goto(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise PlaywrightTimeoutError("timeout")

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    class TimeoutProvider(PlaywrightStoreProvider):
        source_code = "timeout"
        result_selector = ".offer"

        def build_url(self, query):
            return "https://example.test/search"

        async def extract(self, page, collected_at):
            raise AssertionError("navigation failure must happen before extraction")

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "timeout", "GPU", NOW)
    provider = TimeoutProvider(
        clock=lambda: NOW,
        retry_policy=RetryPolicy(
            max_attempts=3,
            base_delay_seconds=0.001,
            max_delay_seconds=0.001,
        ),
    )

    with pytest.raises(ProviderNavigationError):
        asyncio.run(provider.collect(request))

    assert calls == 3
