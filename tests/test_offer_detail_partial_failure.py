"""Uma capability quebrada não pode apagar evidência boa de outra."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.collection import MarketplacePartyKind, RawCollectedOffer, RawInstallmentOption
from app.collection.providers.base import PlaywrightStoreProvider


@pytest.mark.parametrize(
    "failed_hook",
    [
        "parties",
        "condition",
        "availability",
        "seller_name",
        "seller_id",
        "installments",
        "rating",
    ],
)
def test_partial_detail_failure_preserves_card_and_other_capabilities(failed_hook):
    calls = []
    options = (RawInstallmentOption(installment_count=5, raw_amount="R$ 25,00"),)

    def answer(name, value):
        calls.append(name)
        if name == failed_hook:
            raise RuntimeError("isolated capability failure")
        return value

    class Provider(PlaywrightStoreProvider):
        source_code = "terabyte"

        async def resolve_marketplace_parties(self, page):
            return answer(
                "parties",
                (
                    MarketplacePartyKind.MARKETPLACE_PARTNER,
                    MarketplacePartyKind.PLATFORM,
                ),
            )

        async def resolve_offer_condition(self, page):
            return answer("condition", "Usado")

        async def resolve_offer_availability(self, page):
            return answer("availability", "Esgotado")

        async def resolve_seller_name(self, page):
            return answer("seller_name", "Novo vendedor")

        async def resolve_seller_external_id(self, page):
            return answer("seller_id", "new-id")

        async def resolve_installment_options(self, page):
            return answer("installments", options)

        async def resolve_offer_rating(self, page):
            return answer("rating", ("4.8", "90"))

    class Page:
        async def goto(self, url, **kwargs):
            calls.append("navigate")
            return SimpleNamespace(status=200)

    class Transport:
        @asynccontextmanager
        async def open_blank_page(self):
            yield Page()

    card = RawCollectedOffer(
        source_code="terabyte",
        url="https://www.terabyteshop.com.br/produto/123",
        title="Produto",
        collected_at=datetime.now(UTC),
        raw_price="R$ 100,00",
        raw_currency="BRL",
        raw_condition="Novo",
        raw_availability="Disponível",
        seller_name="Vendedor card",
        seller_external_id="card-id",
        seller_kind=MarketplacePartyKind.PLATFORM,
        fulfillment_kind=MarketplacePartyKind.PLATFORM,
        raw_rating_average="4.0",
        raw_review_count="20",
        installment_options=(
            RawInstallmentOption(
                installment_count=2, raw_amount="R$ 60,00", raw_total_amount="R$ 120,00"
            ),
        ),
    )
    provider = Provider(cdp_transport=Transport())
    result = asyncio.run(provider.enrich_offer_details((card,)))[0]
    assert calls.count("navigate") == 1
    assert len(calls) == 8
    assert result.raw_price == card.raw_price
    assert result.raw_condition == (
        card.raw_condition if failed_hook == "condition" else "Usado"
    )
    assert result.raw_availability == (
        card.raw_availability if failed_hook == "availability" else "Esgotado"
    )
    assert result.seller_name == (
        card.seller_name if failed_hook == "seller_name" else "Novo vendedor"
    )
    assert result.seller_external_id == (
        card.seller_external_id if failed_hook == "seller_id" else "new-id"
    )
    assert result.seller_kind == (
        card.seller_kind
        if failed_hook == "parties"
        else MarketplacePartyKind.MARKETPLACE_PARTNER
    )
    assert result.raw_rating_average == (
        card.raw_rating_average if failed_hook == "rating" else "4.8"
    )
    assert result.raw_review_count == (
        card.raw_review_count if failed_hook == "rating" else "90"
    )
    assert result.installment_options == (
        card.installment_options
        if failed_hook == "installments"
        else card.installment_options + options
    )


@pytest.mark.parametrize("failure", [None, 408, 503, "timeout"])
def test_failed_detail_does_not_prevent_next_offer(failure):
    visited = []

    class Page:
        async def goto(self, url, **kwargs):
            visited.append(url)
            if len(visited) == 1:
                if failure == "timeout":
                    raise TimeoutError("failed detail")
                return None if failure is None else SimpleNamespace(status=failure)
            return SimpleNamespace(status=200)

    class Transport:
        @asynccontextmanager
        async def open_blank_page(self):
            yield Page()

    class Provider(PlaywrightStoreProvider):
        source_code = "terabyte"

        async def resolve_installment_options(self, page):
            return (RawInstallmentOption(installment_count=4, raw_amount="R$ 30,00"),)

        async def resolve_offer_rating(self, page):
            return None

    offers = tuple(
        RawCollectedOffer(
            source_code="terabyte",
            url=f"https://www.terabyteshop.com.br/produto/{i}",
            title="Produto",
            collected_at=datetime.now(UTC),
            raw_price=f"R$ {i}00,00",
            raw_currency="BRL",
        )
        for i in (1, 2)
    )
    provider = Provider(
        cdp_transport=Transport(),
        detail_request_min_delay_seconds=0,
        detail_request_max_delay_seconds=0,
    )
    result = asyncio.run(provider.enrich_offer_details(offers))
    assert visited == [item.url for item in offers]
    assert result[0] == offers[0]
    assert result[1].raw_price == offers[1].raw_price
    assert result[1].installment_options[0].installment_count == 4
