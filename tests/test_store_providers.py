import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.collection import (
    AmazonProvider,
    BrowserSession,
    CollectionRequest,
    KabumProvider,
    MagaluProvider,
    MarketplacePartyKind,
    OfferCondition,
    PichauProvider,
    PriceNormalizer,
    ProviderBlockedError,
    ProviderNavigationError,
    RawCollectedOffer,
    TerabyteProvider,
)
from app.collection.providers.base import PlaywrightStoreProvider
from app.collection.providers.magalu_transport import MagaluSearchTransportError
from app.core.resilience import RetryPolicy
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from scripts.validate_store_providers import should_use_headed

NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)

CASES = (
    (
        PichauProvider,
        '<a data-cy="list-product" href="https://www.pichau.com.br/gpu-x"><img class="mui-rfxowm-media" src="https://media.pichau.com.br/gpu.jpg"><h2>GPU Pichau</h2><div class="price_vista">R$ 1.999,90</div></a>',
        "gpu-x",
        "https://media.pichau.com.br/gpu.jpg",
    ),
    (
        TerabyteProvider,
        '<div class="product-item"><img class="image-thumbnail" src="https://img.terabyteshop.com.br/gpu.jpg"><a class="product-item__name" href="https://www.terabyteshop.com.br/produto/123/gpu">GPU Tera</a><div class="product-item__new-price"><span>R$ 2.099,90</span></div></div>',
        "123",
        "https://img.terabyteshop.com.br/gpu.jpg",
    ),
    (
        AmazonProvider,
        '<div data-component-type="s-search-result" data-asin="B0123"><img class="s-image" src="https://m.media-amazon.com/gpu.jpg"><h2><a href="https://www.amazon.com.br/dp/B0123">GPU Amazon</a></h2><span class="a-price"><span class="a-offscreen">R$ 2.199,90</span></span><span>Prime e Entrega GRÁTIS</span><button>Adicionar ao carrinho</button></div>',
        "B0123",
        "https://m.media-amazon.com/gpu.jpg",
    ),
    (
        KabumProvider,
        '<main><a href="https://www.kabum.com.br/produto/456/gpu"><img src="https://images.kabum.com.br/produtos/fotos/456/gpu.jpg"><span class="line-clamp-2">GPU Kabum</span><span class="text-base font-semibold">R$</span><span class="text-base font-semibold">2.299,90</span><span>Vendido por Kabum</span></a></main>',
        "456",
        "https://images.kabum.com.br/produtos/fotos/456/gpu.jpg",
    ),
    (
        MagaluProvider,
        '<a data-testid="product-card-link" href="https://www.magazineluiza.com.br/gpu-magalu/p/abc123/in/gpu/?seller_id=magazineluiza">'
        '<img data-testid="product-card-media" src="https://a-static.mlcdn.com.br/gpu.jpg">'
        '<h3 data-testid="product-card-title">GPU Magalu</h3>'
        '<div data-testid="product-card-price-final">'
        '<span data-testid="price-value-currency">R$</span>'
        '<span data-testid="price-value-integer">2.499</span>'
        '<span data-testid="price-value-split-cents-fraction">90</span></div>'
        '<span data-testid="rating-score">4.8</span>'
        '<span data-testid="rating-label">(123)</span>'
        '<div data-testid="product-card-price-installment">10x de R$ 249,99 sem juros</div>'
        '<div data-testid="product-card-tags">Frete Grátis</div></a>',
        "abc123",
        "https://a-static.mlcdn.com.br/gpu.jpg",
    ),
)


@pytest.mark.parametrize(("provider_type", "html", "external_id", "image_url"), CASES)
def test_extracts_sanitized_store_card(
    provider_type, html, external_id, image_url
) -> None:
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
    assert offers[0].image_url == image_url
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


def test_magalu_card_preserves_explicit_seller_condition_rating_and_installment() -> (
    None
):
    html = (
        '<a data-testid="product-card-link" '
        'href="https://www.magazineluiza.com.br/usado-celular/p/used123/te/ce/?seller_id=ecophone">'
        '<h3 data-testid="product-card-title">Usado: Celular - Excelente</h3>'
        '<span data-testid="price-value-currency">R$</span>'
        '<span data-testid="price-value-integer">3.314</span>'
        '<span data-testid="price-value-split-cents-fraction">15</span>'
        '<span data-testid="rating-score">4.6</span>'
        '<span data-testid="rating-label">(38)</span>'
        '<div data-testid="product-card-price-installment">'
        "10x de R$ 389,90 sem juros</div></a>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await MagaluProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]
    normalized = PriceNormalizer().normalize_offer(offer)

    assert offer.seller_external_id == "ecophone"
    assert offer.seller_kind is MarketplacePartyKind.MARKETPLACE_PARTNER
    assert normalized.condition is OfferCondition.USED
    assert normalized.rating_average == Decimal("4.6")
    assert normalized.review_count == 38
    assert offer.installment_options[0].installment_count == 10


def test_magalu_card_without_rating_preserves_unknown_instead_of_zero() -> None:
    html = (
        '<a data-testid="product-card-link" '
        'href="https://www.magazineluiza.com.br/celular/p/new123/te/ce/?seller_id=magazineluiza">'
        '<h3 data-testid="product-card-title">Celular novo</h3>'
        '<span data-testid="price-value-currency">R$</span>'
        '<span data-testid="price-value-integer">2.499</span>'
        '<span data-testid="price-value-split-cents-fraction">90</span></a>'
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await MagaluProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]
    normalized = PriceNormalizer().normalize_offer(offer)

    assert normalized.rating_average is None
    assert normalized.review_count is None


def test_magalu_search_uses_ssr_json_and_returns_multiple_without_playwright(
    monkeypatch,
) -> None:
    items = [
        {
            "id": f"product-{position}",
            "offerId": f"offer-{position}",
            "path": f"/produto-{position}/p/product-{position}/",
            "title": f"Galaxy S24 Ultra 512GB oferta {position}",
            "image": f"https://a-static.mlcdn.com.br/{{w}}x{{h}}/item-{position}.jpg",
            "available": True,
            "reviewRating": 4.8 if position == 1 else None,
            "reviewCount": 12 if position == 1 else None,
            "offers": [
                {
                    "seller": {"id": "magazineluiza" if position == 1 else "parceiro"},
                    "bestPrice": {"totalAmount": 5000 + position},
                    "bestInstallmentPlan": {
                        "installment": 10,
                        "installmentAmount": 500 + position,
                        "totalAmount": 5000 + position,
                        "paymentMethodDescription": "sem juros",
                    },
                    "badges": [],
                }
            ],
        }
        for position in range(1, 4)
    ]
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"data": {"search": {"items": items}}}}})
        + "</script>"
    )

    class StaticTransport:
        async def fetch_html(self, url: str) -> str:
            assert url.startswith("https://www.magazineluiza.com.br/busca/")
            return html

    class ForbiddenSession:
        def __init__(self, settings):
            raise AssertionError("Playwright must not run when SSR JSON is valid")

    monkeypatch.setattr(
        "app.collection.providers.base.BrowserSession", ForbiddenSession
    )
    provider = MagaluProvider(
        clock=lambda: NOW,
        search_transport=StaticTransport(),
        availability_fallback_max_candidates=0,
    )
    request = CollectionRequest(uuid4(), "magalu", "Galaxy S24 Ultra", NOW)

    result = asyncio.run(provider.collect(request))

    assert len(result.offers) == 3
    assert result.offers[0].seller_kind is MarketplacePartyKind.PLATFORM
    assert result.offers[0].raw_rating_average == "4.8"
    assert result.offers[1].seller_kind is MarketplacePartyKind.MARKETPLACE_PARTNER
    assert result.offers[1].raw_rating_average is None
    assert result.offers[1].raw_review_count is None


def test_magalu_transport_failure_does_not_use_playwright_fallback(monkeypatch) -> None:
    calls = 0

    class FailedTransport:
        async def fetch_html(self, url: str) -> str:
            nonlocal calls
            calls += 1
            raise MagaluSearchTransportError("CDP unavailable")

    async def forbidden_fallback(self, request):
        raise AssertionError("Magalu must not use the common Playwright fallback")

    monkeypatch.setattr(
        "app.collection.providers.base.PlaywrightStoreProvider._collect_once",
        forbidden_fallback,
    )
    provider = MagaluProvider(clock=lambda: NOW, search_transport=FailedTransport())

    with pytest.raises(MagaluSearchTransportError, match="CDP unavailable"):
        asyncio.run(
            provider.collect(CollectionRequest(uuid4(), "magalu", "Produto", NOW))
        )
    assert calls == 1


def test_magalu_does_not_open_managed_browser_for_detail_enrichment(monkeypatch) -> None:
    class Session:
        def __init__(self, settings):
            raise AssertionError("Magalu detail must remain on the CDP SSR result")

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    offer = RawCollectedOffer(
        source_code="magalu",
        url="https://www.magazineluiza.com.br/produto/p/basic/",
        title="Resultado básico",
        collected_at=NOW,
        raw_price="R$ 100,00",
    )

    assert asyncio.run(MagaluProvider().enrich_offer_details((offer,))) == (offer,)


@pytest.mark.parametrize(
    ("text", "seller", "fulfillment"),
    (
        (
            "Vendido e entregue por Magalu",
            MarketplacePartyKind.PLATFORM,
            MarketplacePartyKind.PLATFORM,
        ),
        (
            "Vendido por Shop Next e entregue por Magalu",
            MarketplacePartyKind.MARKETPLACE_PARTNER,
            MarketplacePartyKind.PLATFORM,
        ),
        (
            "Vendido por Loja Parceira e entregue por Loja Parceira",
            MarketplacePartyKind.MARKETPLACE_PARTNER,
            MarketplacePartyKind.MARKETPLACE_PARTNER,
        ),
        (
            "Sem informação comercial",
            MarketplacePartyKind.UNKNOWN,
            MarketplacePartyKind.UNKNOWN,
        ),
    ),
)
def test_magalu_detail_classifies_only_explicit_marketplace_parties(
    text: str,
    seller: MarketplacePartyKind,
    fulfillment: MarketplacePartyKind,
) -> None:
    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(f"<body>{text}</body>")
            parties = await MagaluProvider().resolve_marketplace_parties(page)
            seller_name = await MagaluProvider().resolve_seller_name(page)
            return parties, seller_name

    parties, seller_name = asyncio.run(scenario())

    assert parties == (seller, fulfillment)
    assert seller_name == (
        text.split(" e entregue", 1)[0].removeprefix("Vendido por ")
        if text.startswith("Vendido por ")
        else "Magalu"
        if text.startswith("Vendido e entregue")
        else None
    )


def test_magalu_detail_reuses_structured_condition_and_availability() -> None:
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","itemCondition":"https://schema.org/NewCondition",'
        '"offers":{"availability":"https://schema.org/InStock"}}'
        "</script>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            provider = MagaluProvider()
            return (
                await provider.resolve_offer_condition(page),
                await provider.resolve_offer_availability(page),
            )

    assert asyncio.run(scenario()) == ("Novo", "Disponível")


def test_amazon_collects_exact_rating_evidence_from_card_accessibility() -> None:
    html = (
        '<div data-component-type="s-search-result" data-asin="B0RATING">'
        '<h2><a href="https://www.amazon.com.br/dp/B0RATING">Produto</a></h2>'
        '<span class="a-price"><span class="a-offscreen">R$ 99,90</span></span>'
        '<a aria-label="4,8 de 5 estrelas, detalhes da classificação"></a>'
        '<a href="#customerReviews" aria-label="2.256 classificações">'
        "(2,2 mil)</a></div>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await AmazonProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]
    normalized = PriceNormalizer().normalize_offer(offer)

    assert normalized.rating_average == Decimal("4.8")
    assert normalized.review_count == 2256


@pytest.mark.parametrize(
    ("provider_type", "html"),
    (
        (
            PichauProvider,
            '<a data-cy="list-product" href="https://pichau.com.br/item">'
            '<h2>Produto</h2><span class="price_vista">R$ 100,00</span>'
            '<meta itemprop="ratingValue" content="4.7">'
            '<meta itemprop="reviewCount" content="82"></a>',
        ),
        (
            TerabyteProvider,
            '<div class="product-item"><a class="product-item__name" '
            'href="https://terabyteshop.com.br/produto/1/item">Produto</a>'
            '<div class="product-item__new-price"><span>R$ 100,00</span></div>'
            '<meta itemprop="ratingValue" content="4.7">'
            '<meta itemprop="reviewCount" content="82"></div>',
        ),
        (
            AmazonProvider,
            '<div data-component-type="s-search-result" data-asin="B0CARD">'
            '<h2><a href="https://amazon.com.br/dp/B0CARD">Produto</a></h2>'
            '<span class="a-price"><span class="a-offscreen">R$ 100,00</span></span>'
            '<a aria-label="4,7 de 5 estrelas"></a>'
            '<a href="#customerReviews" aria-label="82 avaliações"></a></div>',
        ),
        (
            KabumProvider,
            '<main><a href="https://kabum.com.br/produto/1/item">'
            '<span class="line-clamp-2">Produto</span>'
            '<span class="text-base font-semibold">R$ 100,00</span>'
            '<meta itemprop="ratingValue" content="4.7">'
            '<meta itemprop="reviewCount" content="82"></a></main>',
        ),
    ),
)
def test_all_store_cards_share_explicit_rating_contract(provider_type, html) -> None:
    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await provider_type().extract(page, NOW)

    normalized = PriceNormalizer().normalize_offer(asyncio.run(scenario())[0])

    assert normalized.rating_average == Decimal("4.7")
    assert normalized.review_count == 82


def test_common_detail_rating_reads_five_star_aggregate_rating() -> None:
    html = (
        '<script type="application/ld+json">'
        '{"@type":"Product","aggregateRating":{"@type":"AggregateRating",'
        '"ratingValue":"4.9","reviewCount":317,"bestRating":5}}'
        "</script>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await PichauProvider().resolve_offer_rating(page)

    assert asyncio.run(scenario()) == ("4.9", "317")


def test_amazon_collects_only_explicit_card_condition_and_seller_kind() -> None:
    html = (
        '<div data-component-type="s-search-result" data-asin="B0USED">'
        '<h2><a href="https://www.amazon.com.br/dp/B0USED">Produto</a></h2>'
        '<span class="a-price"><span class="a-offscreen">R$ 99,90</span></span>'
        '<div>Usado</div><span aria-label="Vendido por Amazon.com.br">'
        "Vendido por Amazon.com.br</span></div>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await AmazonProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]
    normalized = PriceNormalizer().normalize_offer(offer)

    assert normalized.condition is OfferCondition.USED
    assert offer.seller_kind is MarketplacePartyKind.PLATFORM


def test_amazon_maps_explicit_seminovo_title_suffix_to_used() -> None:
    html = (
        '<div data-component-type="s-search-result" data-asin="B0SEMI">'
        '<h2><a href="https://www.amazon.com.br/dp/B0SEMI">'
        "Samsung Galaxy S24 Ultra (Seminovo)</a></h2>"
        '<span class="a-price"><span class="a-offscreen">R$ 4.255,05</span></span>'
        "<div>Mais opções de compra: produtos novos e usados</div></div>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await AmazonProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]
    normalized = PriceNormalizer().normalize_offer(offer)

    assert normalized.condition is OfferCondition.USED


def test_amazon_without_selected_used_marker_defaults_to_new() -> None:
    html = (
        '<div data-component-type="s-search-result" data-asin="B0UNKNOWN">'
        '<h2><a href="https://www.amazon.com.br/dp/B0UNKNOWN">Produto</a></h2>'
        '<span class="a-price"><span class="a-offscreen">R$ 99,90</span></span>'
        "<div>Mais opções de compra: produtos novos e usados</div></div>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await AmazonProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]
    normalized = PriceNormalizer().normalize_offer(offer)

    assert normalized.condition is OfferCondition.NEW


@pytest.mark.parametrize(
    ("explicit_label", "expected"),
    [
        ("Renewed", OfferCondition.REFURBISHED),
        ("Seminovo - Excelente", OfferCondition.USED),
    ],
)
def test_amazon_maps_explicit_renewed_and_seminovo_grade_labels(
    explicit_label: str, expected: OfferCondition
) -> None:
    html = (
        '<div data-component-type="s-search-result" data-asin="B0GRADE">'
        '<h2><a href="https://www.amazon.com.br/dp/B0GRADE">Produto</a></h2>'
        f"<div>{explicit_label}</div>"
        '<span class="a-price"><span class="a-offscreen">R$ 99,90</span></span>'
        "</div>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await AmazonProvider().extract(page, NOW)

    offer = asyncio.run(scenario())[0]
    normalized = PriceNormalizer().normalize_offer(offer)

    assert normalized.condition is expected


def test_amazon_reads_explicit_condition_field_from_existing_detail_page() -> None:
    html = "<body><div>Vendido por Amazon.com.br</div><div>Condição</div><div>Renewed</div></body>"

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await AmazonProvider().resolve_offer_condition(page)

    assert asyncio.run(scenario()) == "Renewed"


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


def test_pichau_resolve_product_availability_branches() -> None:
    async def evidence(html: str) -> str | None:
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await PichauProvider().resolve_product_availability(page)

    assert (
        asyncio.run(evidence("<body>Produto Esgotado no momento</body>")) == "Esgotado"
    )
    assert (
        asyncio.run(evidence("<body><button>Comprar</button></body>")) == "Disponível"
    )
    assert asyncio.run(evidence("<body>Sem nenhuma evidência clara</body>")) is None


def test_terabyte_resolve_product_availability_branches() -> None:
    async def evidence(html: str) -> str | None:
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await TerabyteProvider().resolve_product_availability(page)

    assert asyncio.run(evidence("<body>Produto Indisponível</body>")) == "Esgotado"
    assert (
        asyncio.run(evidence('<body><button class="tbt_cart">x</button></body>'))
        == "Disponível"
    )
    assert asyncio.run(evidence("<body>Sem nenhuma evidência clara</body>")) is None


def test_kabum_resolve_product_availability_branches() -> None:
    async def evidence(html: str) -> str | None:
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await KabumProvider().resolve_product_availability(page)

    assert asyncio.run(evidence("<body>Produto esgotado</body>")) == "Esgotado"
    assert asyncio.run(evidence("<body>Restam 3 Unid.</body>")) == "Disponível"
    assert (
        asyncio.run(evidence("<body><button>Comprar</button></body>")) == "Disponível"
    )
    assert asyncio.run(evidence("<body>Sem nenhuma evidência clara</body>")) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("Amazon.com.br", MarketplacePartyKind.PLATFORM),
        ("GX Group ⭐⭐⭐⭐⭐", MarketplacePartyKind.MARKETPLACE_PARTNER),
        (None, MarketplacePartyKind.UNKNOWN),
    ),
)
def test_amazon_classifies_combined_merchant_detail(value, expected) -> None:
    block = (
        '<div class="offer-display-feature-text" '
        'offer-display-feature-name="desktop-merchant-info">'
        f'<span class="offer-display-feature-text-message">{value}</span></div>'
        if value is not None
        else "<body>Sem informação comercial</body>"
    )

    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(block)
            return await AmazonProvider().resolve_marketplace_parties(page)

    assert asyncio.run(scenario()) == (expected, expected)


@pytest.mark.parametrize(
    ("body", "expected"),
    (
        ("Vendido e entregue por: KaBuM!", MarketplacePartyKind.PLATFORM),
        (
            "Vendido e entregue por: Loja Parceira",
            MarketplacePartyKind.MARKETPLACE_PARTNER,
        ),
        ("Sem informação comercial", MarketplacePartyKind.UNKNOWN),
    ),
)
def test_kabum_classifies_combined_merchant_detail(body, expected) -> None:
    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(f"<body>{body}</body>")
            return await KabumProvider().resolve_marketplace_parties(page)

    assert asyncio.run(scenario()) == (expected, expected)


def test_builds_encoded_source_urls() -> None:
    assert "RTX+5070" in AmazonProvider().build_url("RTX 5070")
    assert "RTX+5070" in PichauProvider().build_url("RTX 5070")
    assert "RTX+5070" in TerabyteProvider().build_url("RTX 5070")
    assert "RTX-5070" in KabumProvider().build_url("RTX 5070")


def test_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        KabumProvider(max_offers=0)


def test_rejects_negative_availability_fallback_max_candidates() -> None:
    with pytest.raises(ValueError):
        KabumProvider(availability_fallback_max_candidates=-1)


def test_rejects_negative_marketplace_party_max_candidates() -> None:
    with pytest.raises(ValueError):
        KabumProvider(marketplace_party_max_candidates=-1)


def test_rejects_negative_installment_option_max_candidates() -> None:
    with pytest.raises(ValueError):
        KabumProvider(installment_option_max_candidates=-1)


# ---------------------------------------------------------------------------
# TASK-089 (DEC-069): enrich_installment_options -- mesma disciplina de
# enrich_marketplace_parties (limitado, ordenado, sequencial, para no bloqueio).
# ---------------------------------------------------------------------------


def test_installment_enrichment_is_bounded_sorted_and_sequential(monkeypatch) -> None:
    from app.collection import InstallmentInterestKind, RawInstallmentOption

    visited: list[str] = []

    class Response:
        status = 200

    class Page:
        async def goto(self, url, **kwargs):
            visited.append(url)
            return Response()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    class Provider(PichauProvider):
        async def resolve_installment_options(self, page):
            return (
                RawInstallmentOption(
                    installment_count=1,
                    raw_amount="R$ 1,00",
                    interest_kind=InstallmentInterestKind.INTEREST_FREE,
                ),
            )

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    offers = tuple(
        RawCollectedOffer(
            source_code="pichau",
            url=f"https://example.invalid/{price}",
            title=f"Produto {price}",
            collected_at=NOW,
            raw_price=f"R$ {price},00",
            raw_currency="BRL",
        )
        for price in (40, 10, 30, 20)
    )

    enriched = asyncio.run(Provider().enrich_installment_options(offers))

    assert visited == [
        "https://example.invalid/10",
        "https://example.invalid/20",
        "https://example.invalid/30",
    ]
    assert sum(len(item.installment_options) == 1 for item in enriched) == 3
    assert enriched[0].installment_options == ()  # quarto preço não foi avaliado


def test_installment_enrichment_stops_after_block(monkeypatch) -> None:
    visited: list[str] = []

    class Response:
        status = 403

    class Page:
        async def goto(self, url, **kwargs):
            visited.append(url)
            return Response()

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
    offers = tuple(
        RawCollectedOffer(
            source_code="pichau",
            url=f"https://example.invalid/{index}",
            title=f"Produto {index}",
            collected_at=NOW,
            raw_price=f"R$ {index},00",
            raw_currency="BRL",
        )
        for index in (1, 2, 3)
    )

    enriched = asyncio.run(PichauProvider().enrich_installment_options(offers))

    assert visited == ["https://example.invalid/1"]
    assert all(item.installment_options == () for item in enriched)


def test_installment_enrichment_skips_navigation_when_provider_has_no_hook() -> None:
    """Amazon/KaBuM! não sobrescrevem `resolve_installment_options` --
    `enrich_installment_options` nunca abre `BrowserSession`."""
    offers = (
        RawCollectedOffer(
            source_code="amazon",
            url="https://example.invalid/1",
            title="Produto",
            collected_at=NOW,
            raw_price="R$ 10,00",
            raw_currency="BRL",
        ),
    )

    enriched = asyncio.run(AmazonProvider().enrich_installment_options(offers))

    assert enriched == offers


def test_marketplace_enrichment_is_bounded_sorted_and_sequential(monkeypatch) -> None:
    visited: list[str] = []

    class Response:
        status = 200

    class Page:
        async def goto(self, url, **kwargs):
            visited.append(url)
            return Response()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    class Provider(AmazonProvider):
        async def resolve_marketplace_parties(self, page):
            return (MarketplacePartyKind.PLATFORM, MarketplacePartyKind.PLATFORM)

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    offers = tuple(
        RawCollectedOffer(
            source_code="amazon",
            url=f"https://example.invalid/{price}",
            title=f"Produto {price}",
            collected_at=NOW,
            raw_price=f"R$ {price},00",
            raw_currency="BRL",
        )
        for price in (40, 10, 30, 20)
    )

    enriched = asyncio.run(Provider().enrich_marketplace_parties(offers))

    assert visited == [
        "https://example.invalid/10",
        "https://example.invalid/20",
        "https://example.invalid/30",
    ]
    assert (
        sum(item.seller_kind is MarketplacePartyKind.PLATFORM for item in enriched) == 3
    )
    assert enriched[0].seller_kind is None  # quarto preço não foi avaliado


def test_unified_detail_enrichment_opens_each_offer_only_once(monkeypatch) -> None:
    from app.collection import InstallmentInterestKind, RawInstallmentOption

    visited: list[str] = []

    class Response:
        status = 200

    class Page:
        async def goto(self, url, **kwargs):
            visited.append(url)
            return Response()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    class Provider(PlaywrightStoreProvider):
        source_code = "future_store"
        result_selector = ".product"

        async def resolve_marketplace_parties(self, page):
            return (MarketplacePartyKind.PLATFORM, MarketplacePartyKind.PLATFORM)

        async def resolve_installment_options(self, page):
            return (
                RawInstallmentOption(
                    installment_count=10,
                    raw_amount="R$ 10,00",
                    interest_kind=InstallmentInterestKind.INTEREST_FREE,
                ),
            )

        async def resolve_offer_availability(self, page):
            return "Disponível"

        async def resolve_seller_name(self, page):
            return "Loja oficial"

        async def resolve_offer_rating(self, page):
            return ("4.7", "82")

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    offer = RawCollectedOffer(
        source_code="future_store",
        url="https://example.invalid/product",
        title="Produto",
        collected_at=NOW,
        raw_price="R$ 100,00",
        raw_currency="BRL",
    )

    enriched = asyncio.run(Provider().enrich_offer_details((offer,)))[0]

    assert visited == [offer.url]
    assert enriched.seller_kind is MarketplacePartyKind.PLATFORM
    assert len(enriched.installment_options) == 1
    assert enriched.raw_rating_average == "4.7"
    assert enriched.raw_review_count == "82"
    assert enriched.raw_availability == "Disponível"
    assert enriched.seller_name == "Loja oficial"


def test_terabyte_unified_detail_enrichment_never_opens_page(monkeypatch) -> None:
    class ForbiddenSession:
        def __init__(self, settings):
            raise AssertionError("Terabyte must not open product pages")

    monkeypatch.setattr(
        "app.collection.providers.base.BrowserSession", ForbiddenSession
    )
    offer = RawCollectedOffer(
        source_code="terabyte",
        url="https://example.invalid/product",
        title="Produto",
        collected_at=NOW,
        raw_price="R$ 100,00",
        raw_currency="BRL",
    )

    assert asyncio.run(TerabyteProvider().enrich_offer_details((offer,))) == (offer,)


def test_marketplace_enrichment_stops_after_block(monkeypatch) -> None:
    visited: list[str] = []

    class Response:
        status = 403

    class Page:
        async def goto(self, url, **kwargs):
            visited.append(url)
            return Response()

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
    offers = tuple(
        RawCollectedOffer(
            source_code="amazon",
            url=f"https://example.invalid/{index}",
            title=f"Produto {index}",
            collected_at=NOW,
            raw_price=f"R$ {index},00",
            raw_currency="BRL",
        )
        for index in (1, 2, 3)
    )

    enriched = asyncio.run(AmazonProvider().enrich_marketplace_parties(offers))

    assert visited == ["https://example.invalid/1"]
    assert all(item.seller_kind is None for item in enriched)


def test_rank_unknown_candidates_skips_invalid_amount() -> None:
    provider = KabumProvider()
    offers = (
        RawCollectedOffer(
            source_code="kabum",
            url="https://x/bad-price",
            title="Preço inválido",
            collected_at=NOW,
            raw_price="R$ abc123",
            raw_currency="BRL",
            raw_availability=None,
        ),
        RawCollectedOffer(
            source_code="kabum",
            url="https://x/good-price",
            title="Preço válido",
            collected_at=NOW,
            raw_price="R$ 10,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
    )

    ranked = provider._rank_unknown_candidates(offers)

    assert [offer.url for offer in ranked] == ["https://x/good-price"]


def test_fallback_disabled_when_max_candidates_is_zero(monkeypatch) -> None:
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
            source_code="fk4",
            url="https://x/only",
            title="A",
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

    class ZeroKProvider(PlaywrightStoreProvider):
        source_code = "fk4"
        result_selector = ".offer"

        def build_url(self, query):
            return "https://example.test/search"

        async def extract(self, page, collected_at):
            return offers

        async def resolve_product_availability(self, page):
            return "Disponível"

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "fk4", "GPU", NOW)
    provider = ZeroKProvider(clock=lambda: NOW, availability_fallback_max_candidates=0)

    result = asyncio.run(provider.collect(request))

    assert goto_calls == ["https://example.test/search"]  # sem fallback
    assert result.offers[0].raw_availability is None


def test_fallback_noop_when_no_unknown_candidates(monkeypatch) -> None:
    goto_calls: list[str] = []

    class Response:
        status = 200

    class First:
        async def wait_for(self, **kwargs):
            return None

    class Locator:
        first = First()

    # já resolvida no card (AVAILABLE): nao ha UNKNOWN candidato ao fallback
    offers = (
        RawCollectedOffer(
            source_code="fk5",
            url="https://x/resolved",
            title="A",
            collected_at=NOW,
            raw_price="R$ 10,00",
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

    class NoUnknownProvider(PlaywrightStoreProvider):
        source_code = "fk5"
        result_selector = ".offer"

        def build_url(self, query):
            return "https://example.test/search"

        async def extract(self, page, collected_at):
            return offers

        async def resolve_product_availability(self, page):
            return "Disponível"

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "fk5", "GPU", NOW)
    provider = NoUnknownProvider(clock=lambda: NOW)

    result = asyncio.run(provider.collect(request))

    assert goto_calls == ["https://example.test/search"]  # sem fallback
    assert result.offers[0].raw_availability == "Disponível"


def test_fallback_treats_resolve_product_availability_exception_as_unresolved(
    monkeypatch,
) -> None:
    class Response:
        status = 200

    class First:
        async def wait_for(self, **kwargs):
            return None

    class Locator:
        first = First()

    offers = (
        RawCollectedOffer(
            source_code="fk6",
            url="https://x/candidate",
            title="A",
            collected_at=NOW,
            raw_price="R$ 10,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
    )

    class Page:
        async def goto(self, url, **kwargs):
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

    class RaisingResolveProvider(PlaywrightStoreProvider):
        source_code = "fk6"
        result_selector = ".offer"

        def build_url(self, query):
            return "https://example.test/search"

        async def extract(self, page, collected_at):
            return offers

        async def resolve_product_availability(self, page):
            raise RuntimeError("evidencia inesperada quebrou a extracao")

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "fk6", "GPU", NOW)
    provider = RaisingResolveProvider(clock=lambda: NOW)

    result = asyncio.run(provider.collect(request))

    assert result.offers[0].raw_availability is None


def test_uses_headed_only_for_protected_sources_by_default() -> None:
    assert should_use_headed("pichau") is True
    assert should_use_headed("terabyte") is True
    assert should_use_headed("magalu") is True
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


def test_pichau_navigates_with_commit_and_cards_release_collection(
    monkeypatch,
) -> None:
    """TASK-075 (correção 2): domcontentloaded demora demais na Pichau; a
    navegação usa "commit" e a coleta segue assim que os cards aparecem,
    sem esperar pelo estado de "zero resultados" (que nunca chega aqui)."""
    goto_kwargs: list[dict] = []

    class Response:
        status = 200

    class HangingFirst:
        async def wait_for(self, **kwargs):
            await asyncio.Event().wait()

    class HangingLocator:
        first = HangingFirst()

    class ImmediateFirst:
        async def wait_for(self, **kwargs):
            return None

    class ImmediateLocator:
        first = ImmediateFirst()

    class Page:
        async def goto(self, url, **kwargs):
            goto_kwargs.append(kwargs)
            return Response()

        def locator(self, selector):
            return ImmediateLocator()

        def get_by_text(self, text, **kwargs):
            return HangingLocator()

    class Session:
        def __init__(self, settings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def new_page(self):
            return Page()

    offers = (
        RawCollectedOffer(
            source_code="pichau",
            url="https://x/gpu",
            title="GPU",
            collected_at=NOW,
            raw_price="R$ 10,00",
            raw_currency="BRL",
            raw_availability=None,
        ),
    )

    monkeypatch.setattr("app.collection.providers.base.BrowserSession", Session)
    request = CollectionRequest(uuid4(), "pichau", "GPU", NOW)
    provider = PichauProvider(clock=lambda: NOW, availability_fallback_max_candidates=0)

    async def _extract(page, collected_at):
        return offers

    monkeypatch.setattr(provider, "extract", _extract)

    result = asyncio.run(provider.collect(request))

    assert goto_kwargs[0]["wait_until"] == "commit"
    assert result.offers == offers


def test_pichau_zero_results_releases_valid_empty_collection(monkeypatch) -> None:
    """Busca válida sem produtos não vira provider_unavailable: coleta
    válida com zero ofertas, extract nunca roda."""

    class Response:
        status = 200

    class HangingFirst:
        async def wait_for(self, **kwargs):
            await asyncio.Event().wait()

    class HangingLocator:
        first = HangingFirst()

    class ImmediateFirst:
        async def wait_for(self, **kwargs):
            return None

    class ImmediateLocator:
        first = ImmediateFirst()

    class Page:
        async def goto(self, url, **kwargs):
            return Response()

        def locator(self, selector):
            return HangingLocator()

        def get_by_text(self, text, **kwargs):
            return ImmediateLocator()

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
    request = CollectionRequest(uuid4(), "pichau", "zzz-nao-existe", NOW)
    provider = PichauProvider(clock=lambda: NOW, availability_fallback_max_candidates=0)

    async def _extract_should_not_run(page, collected_at):
        raise AssertionError("extract nao deve rodar quando o estado eh vazio")

    monkeypatch.setattr(provider, "extract", _extract_should_not_run)

    result = asyncio.run(provider.collect(request))

    assert result.offers == ()


def test_pichau_neither_state_within_timeout_fails_per_existing_policy(
    monkeypatch,
) -> None:
    """Se nem cards nem o estado vazio aparecerem, a política de falha
    continua a mesma de hoje (ProviderBlockedError), não um novo tipo."""

    class Response:
        status = 200

    class FailingFirst:
        async def wait_for(self, **kwargs):
            raise PlaywrightTimeoutError("timeout")

    class FailingLocator:
        first = FailingFirst()

    class Page:
        async def goto(self, url, **kwargs):
            return Response()

        def locator(self, selector):
            return FailingLocator()

        def get_by_text(self, text, **kwargs):
            return FailingLocator()

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
    request = CollectionRequest(uuid4(), "pichau", "GPU", NOW)
    provider = PichauProvider(clock=lambda: NOW, availability_fallback_max_candidates=0)

    async def _extract_should_not_run(page, collected_at):
        raise AssertionError("extract nao deve rodar quando nenhum estado aparece")

    monkeypatch.setattr(provider, "extract", _extract_should_not_run)

    with pytest.raises(ProviderBlockedError):
        asyncio.run(provider.collect(request))


def test_other_providers_keep_domcontentloaded_and_no_empty_state_hook() -> None:
    """A extensão do readiness é opt-in: sem `empty_result_locator`, as
    demais fontes continuam exatamente como hoje."""
    for provider_type in (AmazonProvider, KabumProvider, TerabyteProvider):
        assert provider_type.navigation_wait_until == "domcontentloaded"
        assert provider_type().empty_result_locator(None) is None
