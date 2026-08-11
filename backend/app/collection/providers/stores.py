"""Providers independentes das quatro lojas selecionáveis na V1."""

import re
from datetime import datetime
from urllib.parse import quote, quote_plus

from playwright.async_api import Page

from app.collection.contracts import RawCollectedOffer
from app.collection.providers.base import PlaywrightStoreProvider

_NEGATIVE_STOCK_TEXT = re.compile(r"esgotad[oa]|indispon[ií]vel|sem\s+estoque", re.I)
_BUY_BUTTON_TEXT = (
    "button:has-text('Comprar'), button:has-text('Adicionar ao carrinho')"
)


class PichauProvider(PlaywrightStoreProvider):
    source_code, result_selector = "pichau", 'a[data-cy="list-product"]'

    def build_url(self, query: str) -> str:
        return f"https://www.pichau.com.br/search?q={quote_plus(query)}"

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        rows = await page.locator(self.result_selector).evaluate_all(
            """cards => cards.map(card => { const text = card.innerText || ''; const unavailable = !!card.querySelector('[class*="out_of_stock"]') || /esgotado|indispon[ií]vel|sem estoque/i.test(text); const available = !unavailable && !!card.querySelector('[class*="availability_span_available"]'); return {url: card.href, title: card.querySelector('h2')?.textContent, price: card.querySelector('[class*="price_vista"]')?.textContent, external_id: new URL(card.href).pathname.split('/').filter(Boolean).pop(), availability: unavailable ? 'Esgotado' : available ? 'Disponível' : null, evidence: text}; })"""
        )
        return self.offers_from_rows(rows, collected_at)

    async def resolve_product_availability(self, page: Page) -> str | None:
        text = await page.locator("body").inner_text()
        if _NEGATIVE_STOCK_TEXT.search(text):
            return "Esgotado"
        if await page.locator(_BUY_BUTTON_TEXT).count():
            return "Disponível"
        return None


class TerabyteProvider(PlaywrightStoreProvider):
    source_code, result_selector = "terabyte", 'a.product-item__name[href*="/produto/"]'

    def build_url(self, query: str) -> str:
        return f"https://www.terabyteshop.com.br/busca?str={quote_plus(query)}"

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        rows = await page.locator(self.result_selector).evaluate_all(
            """links => links.map(link => { const card = link.closest('.product-item') || link.parentElement?.parentElement; const match = new URL(link.href).pathname.match(/\\/produto\\/(\\d+)/); const estoque = card?.getAttribute('data-tss-estoque'); const text = card?.innerText || ''; const unavailable = estoque === '0' || /esgotado|indispon[ií]vel/i.test(text); const available = !unavailable && estoque === '1'; return {url: link.href, title: link.textContent || link.title, price: card?.querySelector('.product-item__new-price span')?.textContent, external_id: match?.[1], availability: unavailable ? 'Esgotado' : available ? 'Disponível' : null, evidence: text}; })"""
        )
        return self.offers_from_rows(rows, collected_at)

    async def resolve_product_availability(self, page: Page) -> str | None:
        text = await page.locator("body").inner_text()
        if _NEGATIVE_STOCK_TEXT.search(text):
            return "Esgotado"
        if await page.locator(f"{_BUY_BUTTON_TEXT}, button.tbt_cart").count():
            return "Disponível"
        return None


class AmazonProvider(PlaywrightStoreProvider):
    source_code, result_selector = (
        "amazon",
        '[data-component-type="s-search-result"][data-asin]',
    )

    def build_url(self, query: str) -> str:
        return f"https://www.amazon.com.br/s?k={quote_plus(query)}"

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        rows = await page.locator(self.result_selector).evaluate_all(
            """cards => cards.map(card => { const link = card.querySelector('h2 a, a.a-link-normal.s-no-outline'); const text = card.innerText || ''; const unavailable = /temporariamente fora de estoque|indisponível/i.test(text); const freeShipping = /(?:frete|entrega)\\s+gr[aá]tis/i.test(text) && !/primeiro pedido|com (?:o )?prime|assine (?:o )?prime/i.test(text); return {url: link?.href, title: card.querySelector('h2')?.textContent, price: card.querySelector('.a-price .a-offscreen')?.textContent, external_id: card.dataset.asin, seller: card.querySelector('[aria-label^="Vendido por"]')?.textContent, shipping: freeShipping ? 'Frete grátis' : null, availability: unavailable ? 'Temporariamente fora de estoque' : /adicionar ao carrinho/i.test(text) ? 'Disponível' : null, fulfillment: /prime/i.test(text) ? 'Prime' : null, evidence: text}; })"""
        )
        return self.offers_from_rows(rows, collected_at)


class KabumProvider(PlaywrightStoreProvider):
    source_code, result_selector = "kabum", 'main a[href*="/produto/"]'

    # TASK-075: facet_filters={"kabum_product":["true"]} em base64 --
    # restringe a busca a produtos vendidos e entregues pela própria Kabum,
    # excluindo revenda de terceiro e bundles irrelevantes. Confirmado ao
    # vivo que funciona na busca livre (/busca/{termo}), não só em
    # navegação por categoria.
    _KABUM_PRODUCT_FACET_FILTER = "eyJrYWJ1bV9wcm9kdWN0IjpbInRydWUiXX0="

    def build_url(self, query: str) -> str:
        slug = quote(query.strip().replace(" ", "-"))
        return (
            f"https://www.kabum.com.br/busca/{slug}"
            f"?facet_filters={self._KABUM_PRODUCT_FACET_FILTER}"
        )

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        rows = await page.locator(self.result_selector).evaluate_all(
            """links => links.map(link => { const text = link.innerText || ''; const match = new URL(link.href).pathname.match(/\\/produto\\/(\\d+)/); const title = link.querySelector('span.line-clamp-2')?.textContent; const current = [...link.querySelectorAll('span.text-base.font-semibold')].map(x => x.textContent.trim()).join(' '); const inStock = /restam\\s+\\d+\\s+unid/i.test(text); return {url: link.href, title, price: current || text.match(/R\\$\\s?[\\d.]+,\\d{2}/)?.[0], external_id: match?.[1], seller: text.match(/Vendido por\\s+([^\\n]+)/i)?.[1], shipping: /frete grátis/i.test(text) ? 'Frete grátis' : null, availability: inStock ? 'Disponível' : /indisponível/i.test(text) ? 'Indisponível' : null, evidence: text}; })"""
        )
        unique, seen = [], set()
        for row in rows:
            external_id = row.get("external_id")
            if external_id and external_id not in seen:
                seen.add(external_id)
                unique.append(row)
        return self.offers_from_rows(unique, collected_at)

    async def resolve_product_availability(self, page: Page) -> str | None:
        text = await page.locator("body").inner_text()
        if _NEGATIVE_STOCK_TEXT.search(text):
            return "Esgotado"
        if (
            re.search(r"restam\s+\d+\s+unid", text, re.I)
            or await page.locator(_BUY_BUTTON_TEXT).count()
        ):
            return "Disponível"
        return None
