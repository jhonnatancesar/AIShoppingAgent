"""Providers independentes das quatro lojas selecionáveis na V1."""

import re
from datetime import datetime
from decimal import Decimal
from urllib.parse import quote, quote_plus

from playwright.async_api import Locator, Page

from app.collection.contracts import (
    InstallmentInterestKind,
    MarketplacePartyKind,
    RawCollectedOffer,
    RawInstallmentOption,
)
from app.collection.providers.base import PlaywrightStoreProvider

_NEGATIVE_STOCK_TEXT = re.compile(r"esgotad[oa]|indispon[ií]vel|sem\s+estoque", re.I)
_BUY_BUTTON_TEXT = (
    "button:has-text('Comprar'), button:has-text('Adicionar ao carrinho')"
)
# TASK-075 (correção 2): "Nenhum produto encontrado" é o texto estável do
# estado de busca sem resultados (confirmado via diagnóstico ao vivo em
# 2026-08-11). A página não expõe id/data-cy para esse estado -- só classes
# geradas pelo MUI (ex.: "mui-33avjv"), que não são estáveis entre builds.
_EMPTY_RESULT_TEXT = "Nenhum produto encontrado"

# TASK-089: fraseado do resumo de parcelamento varia por loja ("Em até Nx
# de R$Y Sem juros no cartão", "Nx de R$Y sem juros no cartão", "em até Nx
# de R$Y sem juros", "No PIX ou Nx de R$Y") -- um único padrão cobre as
# quatro, só a contagem/valor importam aqui; "sem juros" é checado à parte
# porque a KaBuM! nunca o menciona.
_INSTALLMENT_SUMMARY_PATTERN = re.compile(
    r"(\d+)\s*x\s*(?:de\s*)?(R\$\s*[\d.,]+)", re.I
)
_INTEREST_FREE_PATTERN = re.compile(r"sem\s+juros", re.I)


def _parse_installment_summary(text: str | None) -> tuple[str | None, str | None, bool]:
    """Extrai (count, amount, interest_free) do resumo de parcelamento já
    visível no card -- nunca inventa nada quando o texto não contém o
    padrão esperado."""
    if not text:
        return None, None, False
    match = _INSTALLMENT_SUMMARY_PATTERN.search(text)
    if match is None:
        return None, None, False
    return match.group(1), match.group(2), bool(_INTEREST_FREE_PATTERN.search(text))


def _apply_installment_summary(
    rows: list[dict], *, text_key: str, total_key: str | None = None
) -> list[dict]:
    """Preenche o schema comum (`installment_count`/`installment_amount`/
    `installment_total`/`installment_interest_free`) lido por
    `offers_from_rows` a partir do texto bruto já extraído no card de cada
    provider -- nunca faz nova consulta à página."""
    for row in rows:
        count, amount, interest_free = _parse_installment_summary(row.get(text_key))
        row["installment_count"] = count
        row["installment_amount"] = amount
        row["installment_interest_free"] = interest_free
        if total_key is not None:
            row["installment_total"] = row.get(total_key)
    return rows


# TASK-089: linha real da tabela de parcelamento individual da Pichau --
# ex.: "1x de R$39.999,99 (com 15% de desconto)" ou "7x de R$319,33 (sem
# juros)". O percentual, quando existe, nunca é assumido -- só o valor
# que a própria linha mostrar.
_PICHAU_INSTALLMENT_ROW = re.compile(
    r"^\s*(\d+)x\s+de\s+(R\$[\d.,]+)\s*\(([^)]*)\)\s*$", re.I
)
_PICHAU_DISCOUNT_PATTERN = re.compile(r"(\d+)\s*%\s*de\s*desconto", re.I)


def _parse_pichau_installment_row(text: str) -> RawInstallmentOption | None:
    match = _PICHAU_INSTALLMENT_ROW.match(text or "")
    if match is None:
        return None
    count_text, amount, note = match.groups()
    try:
        count = int(count_text)
    except ValueError:
        return None
    if count <= 0:
        return None
    discount_match = _PICHAU_DISCOUNT_PATTERN.search(note)
    discount_percent = Decimal(discount_match.group(1)) if discount_match else None
    interest_kind = (
        InstallmentInterestKind.INTEREST_FREE
        if _INTEREST_FREE_PATTERN.search(note)
        else InstallmentInterestKind.UNKNOWN
    )
    try:
        return RawInstallmentOption(
            installment_count=count,
            raw_amount=amount,
            discount_percent=discount_percent,
            interest_kind=interest_kind,
        )
    except Exception:
        return None


class PichauProvider(PlaywrightStoreProvider):
    source_code, result_selector = "pichau", 'a[data-cy="list-product"]'
    # TASK-075 (correção 2): domcontentloaded demora 22-38s (às vezes >45s)
    # na Pichau por causa de terceiros (analytics/ads) alheios ao conteúdo
    # útil; os cards reais já existem em ~9-12s. "commit" + espera explícita
    # pelo seletor/estado vazio reflete o readiness real da página.
    navigation_wait_until = "commit"

    def build_url(self, query: str) -> str:
        return f"https://www.pichau.com.br/search?q={quote_plus(query)}"

    def empty_result_locator(self, page: Page) -> Locator:
        return page.get_by_text(_EMPTY_RESULT_TEXT)

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        rows = await page.locator(self.result_selector).evaluate_all(
            """cards => cards.map(card => { const text = card.innerText || ''; const unavailable = !!card.querySelector('[class*="out_of_stock"]') || /esgotado|indispon[ií]vel|sem estoque/i.test(text); const available = !unavailable && !!card.querySelector('[class*="availability_span_available"]'); const image = card.querySelector('img.mui-rfxowm-media'); return {url: card.href, title: card.querySelector('h2')?.textContent, price: card.querySelector('[class*="price_vista"]')?.textContent, external_id: new URL(card.href).pathname.split('/').filter(Boolean).pop(), image: image?.currentSrc || image?.src, availability: unavailable ? 'Esgotado' : available ? 'Disponível' : null, evidence: text, parcelado: card.querySelector('[class*="price_parcelado_text"]')?.textContent, total: card.querySelector('[class*="price_total"]')?.textContent}; })"""
        )
        rows = _apply_installment_summary(rows, text_key="parcelado", total_key="total")
        return self.offers_from_rows(rows, collected_at)

    async def resolve_product_availability(self, page: Page) -> str | None:
        text = await page.locator("body").inner_text()
        if _NEGATIVE_STOCK_TEXT.search(text):
            return "Esgotado"
        if await page.locator(_BUY_BUTTON_TEXT).count():
            return "Disponível"
        return None

    async def resolve_installment_options(
        self, page: Page
    ) -> tuple[RawInstallmentOption, ...]:
        """TASK-089: tabela "PARCELAMENTO" da página individual (1x até a
        maior quantidade oferecida), com desconto/isenção de juros
        explícitos por linha -- a investigação real confirmou que o
        percentual varia por produto e por quantidade de parcelas, nunca
        um valor fixo. Seletor por substring (`[class*="installment"]`),
        mesmo motivo de `[class*="price_vista"]`: classes MUI não são
        estáveis entre builds."""
        rows = await page.locator(
            '[class*="installmentsWrapper"] > [class*="installment"]'
        ).all_text_contents()
        options: list[RawInstallmentOption] = []
        seen: set[int] = set()
        for text in rows:
            option = _parse_pichau_installment_row(text)
            if option is not None and option.installment_count not in seen:
                seen.add(option.installment_count)
                options.append(option)
        return tuple(options)


class TerabyteProvider(PlaywrightStoreProvider):
    source_code, result_selector = "terabyte", 'a.product-item__name[href*="/produto/"]'

    def build_url(self, query: str) -> str:
        return f"https://www.terabyteshop.com.br/busca?str={quote_plus(query)}"

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        rows = await page.locator(self.result_selector).evaluate_all(
            """links => links.map(link => { const card = link.closest('.product-item') || link.parentElement?.parentElement; const match = new URL(link.href).pathname.match(/\\/produto\\/(\\d+)/); const estoque = card?.getAttribute('data-tss-estoque'); const text = card?.innerText || ''; const unavailable = estoque === '0' || /esgotado|indispon[ií]vel/i.test(text); const available = !unavailable && estoque === '1'; const image = card?.querySelector('img.image-thumbnail'); return {url: link.href, title: link.textContent || link.title, price: card?.querySelector('.product-item__new-price span')?.textContent, external_id: match?.[1], image: image?.currentSrc || image?.src, availability: unavailable ? 'Esgotado' : available ? 'Disponível' : null, evidence: text, juros: card?.querySelector('.product-item__juros')?.textContent}; })"""
        )
        rows = _apply_installment_summary(rows, text_key="juros")
        return self.offers_from_rows(rows, collected_at)

    async def resolve_product_availability(self, page: Page) -> str | None:
        text = await page.locator("body").inner_text()
        if _NEGATIVE_STOCK_TEXT.search(text):
            return "Esgotado"
        if await page.locator(f"{_BUY_BUTTON_TEXT}, button.tbt_cart").count():
            return "Disponível"
        return None

    # Correção (bloqueio Cloudflare, 2026-08-20): `resolve_installment_options`
    # foi removido de propósito -- a Terabyte não navega mais para a página
    # individual do produto só para detalhar a tabela de parcelamento
    # (1x-18x). O parcelamento desta loja agora vem exclusivamente do que
    # `extract()`/`_apply_installment_summary` já capturam no card da busca
    # (mesmo caminho comum a Amazon/KaBuM!). Reduz de até 4 navegações
    # (1 busca + até 3 páginas) para exatamente 1 por execução. A ausência
    # deste método faz `enrich_installment_options` (`providers/base.py`)
    # pular a navegação inteira via
    # `type(self).resolve_installment_options is PlaywrightStoreProvider.resolve_installment_options`
    # -- não é um "desligamento" condicional, é a ausência estrutural do hook.


def _amazon_card_condition(value: object, title: object) -> str | None:
    """Aceita só declaração explícita isolada ou sufixo explícito do título."""
    for line in str(value or "").splitlines():
        normalized = line.strip()
        if re.fullmatch(
            r"novo|usado|recondicionado|renewed|"
            r"seminovo(?:\s*-\s*(?:excelente|bom|aceitável))?",
            normalized,
            re.I,
        ):
            return normalized
    title_match = re.search(
        r"\((novo|usado|seminovo|recondicionado|renewed)\)\s*$",
        str(title or "").strip(),
        re.I,
    )
    if title_match is not None:
        return title_match.group(1)
    # Regra comercial confirmada na validação real da Amazon: a oferta
    # principal é nova salvo marcador explícito de usado/seminovo/renewed.
    return "Novo"


def _amazon_card_seller_kind(value: object) -> str | None:
    """Classifica somente quando o próprio card declara o vendedor."""
    seller = re.sub(r"^Vendido por\s*", "", str(value or "").strip(), flags=re.I)
    if not seller:
        return None
    if seller.casefold() in {"amazon", "amazon.com.br"}:
        return MarketplacePartyKind.PLATFORM.value
    return MarketplacePartyKind.MARKETPLACE_PARTNER.value


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
            """cards => cards.map(card => { const link = card.querySelector('h2 a, a.a-link-normal.s-no-outline'); const text = card.innerText || ''; const unavailable = /temporariamente fora de estoque|indisponível/i.test(text); const freeShipping = /(?:frete|entrega)\\s+gr[aá]tis/i.test(text) && !/primeiro pedido|com (?:o )?prime|assine (?:o )?prime/i.test(text); const image = card.querySelector('img.s-image'); const installmentBlock = card.querySelector('.a-row.a-size-base.a-color-base'); return {url: link?.href, title: card.querySelector('h2')?.textContent, price: card.querySelector('.a-price .a-offscreen')?.textContent, external_id: card.dataset.asin, seller: card.querySelector('[aria-label^="Vendido por"]')?.textContent, image: image?.currentSrc || image?.src, shipping: freeShipping ? 'Frete grátis' : null, availability: unavailable ? 'Temporariamente fora de estoque' : /adicionar ao carrinho/i.test(text) ? 'Disponível' : null, fulfillment: /prime/i.test(text) ? 'Prime' : null, evidence: text, installmentText: installmentBlock?.textContent}; })"""
        )
        for row in rows:
            row["condition"] = _amazon_card_condition(
                row.get("evidence"), row.get("title")
            )
            row["seller_kind"] = _amazon_card_seller_kind(row.get("seller"))
        rows = _apply_installment_summary(rows, text_key="installmentText")
        return self.offers_from_rows(rows, collected_at)

    async def resolve_marketplace_parties(
        self, page: Page
    ) -> tuple[MarketplacePartyKind, MarketplacePartyKind]:
        value = page.locator(
            '.offer-display-feature-text[offer-display-feature-name="desktop-merchant-info"] '
            ".offer-display-feature-text-message"
        )
        text = (await value.first.inner_text()).strip() if await value.count() else ""
        if not text:
            return (MarketplacePartyKind.UNKNOWN, MarketplacePartyKind.UNKNOWN)
        kind = (
            MarketplacePartyKind.PLATFORM
            if text.casefold() in {"amazon", "amazon.com.br"}
            else MarketplacePartyKind.MARKETPLACE_PARTNER
        )
        return (kind, kind)

    async def resolve_offer_condition(self, page: Page) -> str | None:
        """Prioriza o campo explícito `Condição` da página já aberta."""
        text = await page.locator("body").inner_text()
        field = re.search(
            r"(?im)^\s*Condição\s*:?\s*(?:\r?\n\s*)?([^\r\n]+)", text
        )
        if field is not None:
            return field.group(1).strip()
        label = re.search(
            r"(?im)^\s*(Renewed|Seminovo(?:\s*-\s*(?:Excelente|Bom|Aceitável))?)\s*$",
            text,
        )
        return label.group(1).strip() if label is not None else None


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
            """links => links.map(link => { const text = link.innerText || ''; const match = new URL(link.href).pathname.match(/\\/produto\\/(\\d+)/); const title = link.querySelector('span.line-clamp-2')?.textContent; const current = [...link.querySelectorAll('span.text-base.font-semibold')].map(x => x.textContent.trim()).join(' '); const inStock = /restam\\s+\\d+\\s+unid/i.test(text); const image = link.querySelector('img[src*="images.kabum.com.br/produtos/fotos/"]'); const installmentSpan = [...link.querySelectorAll('span')].find(s => /PIX/i.test(s.textContent) && /R\\$/.test(s.textContent) && s.children.length === 0); return {url: link.href, title, price: current || text.match(/R\\$\\s?[\\d.]+,\\d{2}/)?.[0], external_id: match?.[1], seller: text.match(/Vendido por\\s+([^\\n]+)/i)?.[1], image: image?.currentSrc || image?.src, shipping: /frete grátis/i.test(text) ? 'Frete grátis' : null, availability: inStock ? 'Disponível' : /indisponível/i.test(text) ? 'Indisponível' : null, evidence: text, installmentText: installmentSpan?.textContent}; })"""
        )
        rows = _apply_installment_summary(rows, text_key="installmentText")
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

    async def resolve_marketplace_parties(
        self, page: Page
    ) -> tuple[MarketplacePartyKind, MarketplacePartyKind]:
        text = await page.locator("body").inner_text()
        match = re.search(r"Vendido\s+e\s+entregue\s+por:\s*([^\r\n]+)", text, re.I)
        if match is None or not match.group(1).strip():
            return (MarketplacePartyKind.UNKNOWN, MarketplacePartyKind.UNKNOWN)
        seller = match.group(1).strip().rstrip(".")
        kind = (
            MarketplacePartyKind.PLATFORM
            if seller.casefold() in {"kabum", "kabum!"}
            else MarketplacePartyKind.MARKETPLACE_PARTNER
        )
        return (kind, kind)
