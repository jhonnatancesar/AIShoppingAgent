"""TASK-136 (passo 1) -- detalhe de TODOS os candidatos nas lojas que só mostram
popularidade na página do produto (Pichau, Kabum, Mercado Livre), com orçamento de
tempo para as páginas extras."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from app.collection.contracts import RawCollectedOffer
from app.collection.providers.base import PlaywrightStoreProvider
from app.collection.providers.stores import (
    KabumProvider,
    MercadoLivreProvider,
    PichauProvider,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


class _Page:
    def __init__(self, visited: list[str]) -> None:
        self.visited = visited

    async def goto(self, url, **kwargs):
        self.visited.append(url)
        return SimpleNamespace(status=200)


def _provider(*, all_candidates: bool, budget: float = 120.0):
    visited: list[str] = []

    class Transport:
        @asynccontextmanager
        async def open_blank_page(self):
            yield _Page(visited)

    class Provider(PlaywrightStoreProvider):
        source_code = "pichau"
        rating_detail_enabled = True
        popularity_detail_for_all_candidates = all_candidates
        popularity_detail_budget_seconds = budget

        async def resolve_offer_rating(self, page):
            return ("4.9", "10")

    provider = Provider(
        cdp_transport=Transport(),
        detail_request_min_delay_seconds=0,
        detail_request_max_delay_seconds=0,
    )
    return provider, visited


def _offers(count: int) -> tuple[RawCollectedOffer, ...]:
    return tuple(
        RawCollectedOffer(
            source_code="pichau",
            url=f"https://www.pichau.com.br/produto-{n}",
            title=f"Placa {n}",
            collected_at=NOW,
            external_id=str(n),
            raw_price=f"R$ {100 + n},00",
            raw_currency="BRL",
            raw_availability="Disponível",
        )
        for n in range(count)
    )


def test_stores_that_only_show_popularity_on_the_product_page_open_every_candidate() -> (
    None
):
    for provider_class in (PichauProvider, KabumProvider, MercadoLivreProvider):
        assert provider_class.popularity_detail_for_all_candidates is True
    assert PlaywrightStoreProvider.popularity_detail_for_all_candidates is False


def test_default_opens_only_the_three_cheapest_like_before() -> None:
    provider, visited = _provider(all_candidates=False)
    enriched = asyncio.run(provider.enrich_offer_details(_offers(8)))
    assert len(visited) == 3
    assert sum(1 for offer in enriched if offer.raw_rating_average) == 3


def test_all_candidates_are_opened_and_rated() -> None:
    provider, visited = _provider(all_candidates=True)
    enriched = asyncio.run(provider.enrich_offer_details(_offers(8)))
    assert len(visited) == 8
    assert all(offer.raw_rating_average == "4.9" for offer in enriched)
    assert all(offer.raw_review_count == "10" for offer in enriched)


def test_extra_pages_stop_when_the_time_budget_is_over_but_the_first_three_stay() -> (
    None
):
    provider, visited = _provider(all_candidates=True, budget=-1.0)
    enriched = asyncio.run(provider.enrich_offer_details(_offers(8)))
    # 3 originais + a 1ª extra (que abre a janela de orçamento); a seguinte já passou do tempo
    assert len(visited) == 4
    assert sum(1 for offer in enriched if offer.raw_rating_average) == 4
