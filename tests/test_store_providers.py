import asyncio
from datetime import UTC, datetime

import pytest
from app.collection import (
    AmazonProvider,
    BrowserSession,
    KabumProvider,
    PichauProvider,
    TerabyteProvider,
)

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
        '<div data-component-type="s-search-result" data-asin="B0123"><h2><a href="https://www.amazon.com.br/dp/B0123">GPU Amazon</a></h2><span class="a-price"><span class="a-offscreen">R$ 2.199,90</span></span><span>Prime e frete grátis</span></div>',
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


def test_builds_encoded_source_urls() -> None:
    assert "RTX+5070" in AmazonProvider().build_url("RTX 5070")
    assert "RTX+5070" in PichauProvider().build_url("RTX 5070")
    assert "RTX+5070" in TerabyteProvider().build_url("RTX 5070")
    assert "RTX-5070" in KabumProvider().build_url("RTX 5070")


def test_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        KabumProvider(max_offers=0)
