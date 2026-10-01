"""TASK-136 (passo 1) -- nota e avaliações desenhadas no texto do card (Terabyte)."""

from datetime import UTC, datetime

from app.collection.providers.base import PlaywrightStoreProvider, _rating_from_row

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def test_terabyte_star_text_is_read() -> None:
    row = {"evidence": "Placa Mãe MSI ★ ★ ★ ★ ★ 4.9 (84) De: R$ 941,06 por: R$ 799,90"}
    assert _rating_from_row(row) == ("4.9", "84")


def test_thousands_separator_in_the_count_is_removed() -> None:
    assert _rating_from_row({"evidence": "★ ★ ★ ★ ★ 4,8 (1.234) R$ 10"}) == (
        "4,8",
        "1234",
    )


def test_store_selectors_win_over_the_text() -> None:
    row = {
        "rating_average": "4.0",
        "review_count": "20",
        "evidence": "★ ★ ★ ★ ★ 4.9 (84)",
    }
    assert _rating_from_row(row) == ("4.0", "20")


def test_text_without_stars_or_without_the_count_is_never_read() -> None:
    assert _rating_from_row({"evidence": "Placa Mãe 4.9 R$ 799,00"}) == (None, None)
    assert _rating_from_row({"evidence": "★ ★ ★ ★ ★ 4.9 R$ 799,00"}) == (None, None)
    assert _rating_from_row({}) == (None, None)


def test_offers_from_rows_uses_the_text_rating() -> None:
    class Provider(PlaywrightStoreProvider):
        source_code = "terabyte"

    rows = [
        {
            "url": "https://www.terabyteshop.com.br/produto/1",
            "title": "Placa Mãe",
            "price": "R$ 799,90",
            "evidence": "Placa Mãe ★ ★ ★ ★ ★ 4.9 (84) R$ 799,90",
        }
    ]
    offer = Provider().offers_from_rows(rows, NOW)[0]
    assert (offer.raw_rating_average, offer.raw_review_count) == ("4.9", "84")


def test_detail_enrichment_keeps_the_sales_text_and_survives_a_hook_failure() -> None:
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.collection.contracts import RawCollectedOffer

    class Page:
        async def goto(self, url, **kwargs):
            return SimpleNamespace(status=200)

    class Transport:
        @asynccontextmanager
        async def open_blank_page(self):
            yield Page()

    def make(hook):
        class Provider(PlaywrightStoreProvider):
            source_code = "mercadolivre"
            sales_detail_enabled = True

            async def resolve_offer_sales_text(self, page):
                return hook()

        return Provider(cdp_transport=Transport())

    card = RawCollectedOffer(
        source_code="mercadolivre",
        url="https://www.mercadolivre.com.br/p/MLB1",
        title="Placa",
        collected_at=NOW,
        raw_price="R$ 100,00",
        raw_currency="BRL",
        evidence={"card_text": "Placa 4.8"},
    )
    enriched = asyncio.run(
        make(lambda: "Novo  |  +500 vendidos").enrich_offer_details((card,))
    )[0]
    assert enriched.evidence["detail_sales_text"] == "Novo  |  +500 vendidos"
    assert enriched.evidence["card_text"] == "Placa 4.8"

    def boom():
        raise RuntimeError("página mudou")

    untouched = asyncio.run(make(boom).enrich_offer_details((card,)))[0]
    assert "detail_sales_text" not in untouched.evidence
