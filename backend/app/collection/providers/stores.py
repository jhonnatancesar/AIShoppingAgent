"""Providers independentes das lojas selecionáveis na V1."""

import json
import re
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, quote_plus, urlencode, urljoin, urlparse

from playwright.async_api import Locator, Page

from app.collection.contracts import (
    CollectionRequest,
    CollectionResult,
    InstallmentInterestKind,
    MarketplacePartyKind,
    RawCollectedOffer,
    RawInstallmentOption,
)
from app.collection.providers.base import PlaywrightStoreProvider
from app.collection.providers.magalu_transport import (
    MagaluSearchTransport,
    UnavailableMagaluSearchTransport,
)

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
            """cards => cards.map(card => { const text = card.innerText || ''; const unavailable = !!card.querySelector('[class*="out_of_stock"]') || /esgotado|indispon[ií]vel|sem estoque/i.test(text); const available = !unavailable && !!card.querySelector('[class*="availability_span_available"]'); const image = card.querySelector('img.mui-rfxowm-media'); const rating = card.querySelector('[itemprop="ratingValue"], [data-rating], [aria-label*="estrela"]'); const reviews = card.querySelector('[itemprop="reviewCount"], [itemprop="ratingCount"], [aria-label*="avalia"], [aria-label*="classifica"]'); return {url: card.href, title: card.querySelector('h2')?.textContent, price: card.querySelector('[class*="price_vista"]')?.textContent, external_id: new URL(card.href).pathname.split('/').filter(Boolean).pop(), image: image?.currentSrc || image?.src, availability: unavailable ? 'Esgotado' : available ? 'Disponível' : null, evidence: text, rating_average: rating?.getAttribute('content') || rating?.getAttribute('data-rating') || rating?.getAttribute('aria-label'), review_count: reviews?.getAttribute('content') || reviews?.getAttribute('aria-label'), parcelado: card.querySelector('[class*="price_parcelado_text"]')?.textContent, total: card.querySelector('[class*="price_total"]')?.textContent}; })"""
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
            """links => links.map(link => { const card = link.closest('.product-item') || link.parentElement?.parentElement; const match = new URL(link.href).pathname.match(/\\/produto\\/(\\d+)/); const estoque = card?.getAttribute('data-tss-estoque'); const text = card?.innerText || ''; const unavailable = estoque === '0' || /esgotado|indispon[ií]vel/i.test(text); const available = !unavailable && estoque === '1'; const image = card?.querySelector('img.image-thumbnail'); const rating = card?.querySelector('[itemprop="ratingValue"], [data-rating], [aria-label*="estrela"]'); const reviews = card?.querySelector('[itemprop="reviewCount"], [itemprop="ratingCount"], [aria-label*="avalia"], [aria-label*="classifica"]'); return {url: link.href, title: link.textContent || link.title, price: card?.querySelector('.product-item__new-price span')?.textContent, external_id: match?.[1], image: image?.currentSrc || image?.src, availability: unavailable ? 'Esgotado' : available ? 'Disponível' : null, evidence: text, rating_average: rating?.getAttribute('content') || rating?.getAttribute('data-rating') || rating?.getAttribute('aria-label'), review_count: reviews?.getAttribute('content') || reviews?.getAttribute('aria-label'), juros: card?.querySelector('.product-item__juros')?.textContent}; })"""
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
            """cards => cards.map(card => { const link = card.querySelector('h2 a, a.a-link-normal.s-no-outline'); const text = card.innerText || ''; const unavailable = /temporariamente fora de estoque|indisponível/i.test(text); const freeShipping = /(?:frete|entrega)\\s+gr[aá]tis/i.test(text) && !/primeiro pedido|com (?:o )?prime|assine (?:o )?prime/i.test(text); const image = card.querySelector('img.s-image'); const installmentBlock = card.querySelector('.a-row.a-size-base.a-color-base'); return {url: link?.href, title: card.querySelector('h2')?.textContent, price: card.querySelector('.a-price .a-offscreen')?.textContent, external_id: card.dataset.asin, seller: card.querySelector('[aria-label^="Vendido por"]')?.textContent, image: image?.currentSrc || image?.src, shipping: freeShipping ? 'Frete grátis' : null, availability: unavailable ? 'Temporariamente fora de estoque' : /adicionar ao carrinho/i.test(text) ? 'Disponível' : null, fulfillment: /prime/i.test(text) ? 'Prime' : null, evidence: text, rating_average: card.querySelector('a[aria-label*="de 5 estrelas"]')?.getAttribute('aria-label'), review_count: card.querySelector('a[href*="customerReviews"][aria-label]')?.getAttribute('aria-label'), installmentText: installmentBlock?.textContent}; })"""
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
        field = re.search(r"(?im)^\s*Condição\s*:?\s*(?:\r?\n\s*)?([^\r\n]+)", text)
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
            """links => links.map(link => { const text = link.innerText || ''; const match = new URL(link.href).pathname.match(/\\/produto\\/(\\d+)/); const title = link.querySelector('span.line-clamp-2')?.textContent; const current = [...link.querySelectorAll('span.text-base.font-semibold')].map(x => x.textContent.trim()).join(' '); const inStock = /restam\\s+\\d+\\s+unid/i.test(text); const image = link.querySelector('img[src*="images.kabum.com.br/produtos/fotos/"]'); const installmentSpan = [...link.querySelectorAll('span')].find(s => /PIX/i.test(s.textContent) && /R\\$/.test(s.textContent) && s.children.length === 0); const rating = link.querySelector('[itemprop="ratingValue"], [data-rating], [aria-label*="estrela"]'); const reviews = link.querySelector('[itemprop="reviewCount"], [itemprop="ratingCount"], [aria-label*="avalia"], [aria-label*="classifica"]'); return {url: link.href, title, price: current || text.match(/R\\$\\s?[\\d.]+,\\d{2}/)?.[0], external_id: match?.[1], seller: text.match(/Vendido por\\s+([^\\n]+)/i)?.[1], image: image?.currentSrc || image?.src, shipping: /frete grátis/i.test(text) ? 'Frete grátis' : null, availability: inStock ? 'Disponível' : /indisponível/i.test(text) ? 'Indisponível' : null, evidence: text, rating_average: rating?.getAttribute('content') || rating?.getAttribute('data-rating') || rating?.getAttribute('aria-label'), review_count: reviews?.getAttribute('content') || reviews?.getAttribute('aria-label'), installmentText: installmentSpan?.textContent}; })"""
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


_MAGALU_PLATFORM_NAMES = frozenset({"magalu", "magazine luiza", "magazineluiza"})
_MAGALU_PARTIES = re.compile(
    r"^Vendido\s+(?:(?:e\s+entregue\s+por\s+(?P<same>.+))|"
    r"(?:por\s+(?P<seller>.+?)\s+e\s+entregue\s+por\s+(?P<fulfillment>.+)))$",
    re.I,
)


class _MagaluNextDataParser(HTMLParser):
    """Extrai somente o JSON SSR oficial, sem executar scripts da página."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._inside_next_data = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = dict(attrs)
        self._inside_next_data = attributes.get("id") == "__NEXT_DATA__"

    def handle_data(self, data: str) -> None:
        if self._inside_next_data:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script":
            self._inside_next_data = False

    @property
    def payload(self) -> str | None:
        value = "".join(self._parts).strip()
        return value or None


def _magalu_money(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except Exception:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return f"R$ {amount:.2f}".replace(".", ",")


def _magalu_rows_from_next_data(html: str) -> list[dict[str, object]]:
    parser = _MagaluNextDataParser()
    parser.feed(html)
    if parser.payload is None:
        return []
    try:
        document = json.loads(parser.payload)
        items = document["props"]["pageProps"]["data"]["search"]["items"]
    except KeyError, TypeError, ValueError:
        return []
    if not isinstance(items, list):
        return []

    rows: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        offers = item.get("offers")
        offer = offers[0] if isinstance(offers, list) and offers else None
        if not isinstance(offer, dict):
            continue
        seller = offer.get("seller")
        seller_id = seller.get("id") if isinstance(seller, dict) else None
        path = item.get("path")
        url = urljoin("https://www.magazineluiza.com.br", str(path or ""))
        if seller_id:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode({'seller_id': str(seller_id)})}"
        plan = offer.get("bestInstallmentPlan")
        plan = plan if isinstance(plan, dict) else {}
        best_price = offer.get("bestPrice")
        best_price = best_price if isinstance(best_price, dict) else {}
        shipping = item.get("shippingTag")
        shipping = shipping if isinstance(shipping, dict) else {}
        rating = item.get("reviewRating")
        review_count = item.get("reviewCount")
        has_rating = rating is not None and review_count is not None
        installment = plan.get("installment")
        installment_amount = _magalu_money(plan.get("installmentAmount"))
        installment_text = None
        if installment and installment_amount:
            description = str(plan.get("paymentMethodDescription") or "").strip()
            installment_text = (
                f"{installment}x de {installment_amount} {description}".strip()
            )
        image = str(item.get("image") or "").replace("{w}x{h}", "144x137")
        available = item.get("available")
        rows.append(
            {
                "url": url,
                "title": item.get("title"),
                "price": _magalu_money(best_price.get("totalAmount")),
                "external_id": item.get("offerId") or item.get("id"),
                "seller_external_id": seller_id,
                "seller": "Magalu" if seller_id == "magazineluiza" else seller_id,
                "image": image or None,
                "shipping": "Frete grátis" if shipping.get("cost") == 0 else None,
                "availability": (
                    "Disponível"
                    if available is True
                    else "Indisponível"
                    if available is False
                    else None
                ),
                "evidence": json.dumps(
                    {
                        "available": available,
                        "seller": seller_id,
                        "shipping": shipping,
                        "badges": offer.get("badges"),
                    },
                    ensure_ascii=False,
                )[:1000],
                "rating_average": str(rating) if has_rating else None,
                "review_count": str(review_count) if has_rating else None,
                "installmentText": installment_text,
                "installmentTotal": _magalu_money(plan.get("totalAmount")),
            }
        )
    return rows


def _magalu_party_kind(value: str | None) -> MarketplacePartyKind:
    if not value or not value.strip():
        return MarketplacePartyKind.UNKNOWN
    return (
        MarketplacePartyKind.PLATFORM
        if value.strip().rstrip(".").casefold() in _MAGALU_PLATFORM_NAMES
        else MarketplacePartyKind.MARKETPLACE_PARTNER
    )


def _magalu_card_condition(title: object) -> str:
    text = str(title or "").strip()
    if re.search(
        r"(?:^|[\s:(-])(?:recondicionado|refurbished|renewed)(?:$|[\s:)-])",
        text,
        re.I,
    ):
        return "Recondicionado"
    if re.search(r"(?:^|[\s:(-])(?:usado|seminovo)(?:$|[\s:)-])", text, re.I):
        return "Usado"
    return "Novo"


def _magalu_seller_id(url: object) -> str | None:
    values = parse_qs(urlparse(str(url or "")).query).get("seller_id", [])
    return values[0].strip() if values and values[0].strip() else None


def _magalu_parties_from_text(text: str) -> tuple[str | None, str | None]:
    for line in text.splitlines():
        match = _MAGALU_PARTIES.fullmatch(line.strip())
        if match is None:
            continue
        same = match.group("same")
        if same:
            party = same.strip().rstrip(".")
            return party, party
        return (
            match.group("seller").strip().rstrip("."),
            match.group("fulfillment").strip().rstrip("."),
        )
    return None, None


class MagaluProvider(PlaywrightStoreProvider):
    """Provider público da Magalu; marketplace e varejo no mesmo catálogo."""

    source_code = "magalu"
    result_selector = (
        'script#__NEXT_DATA__, a[data-testid="product-card-link"][href*="/p/"]'
    )
    result_wait_uses_navigation_timeout = True

    def __init__(
        self,
        *args,
        search_transport: MagaluSearchTransport | None = None,
        **kwargs,
    ) -> None:
        # Nesta versão todo dado operacional Magalu vem do documento SSR obtido
        # pelo Edge/CDP dedicado. Não abrir Chromium gerenciado nem página de
        # produto como fallback/enriquecimento paralelo.
        kwargs.pop("availability_fallback_max_candidates", None)
        kwargs.pop("marketplace_party_max_candidates", None)
        kwargs.pop("installment_option_max_candidates", None)
        super().__init__(
            *args,
            availability_fallback_max_candidates=0,
            marketplace_party_max_candidates=0,
            installment_option_max_candidates=0,
            **kwargs,
        )
        self._search_transport = search_transport or UnavailableMagaluSearchTransport()

    def build_url(self, query: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", query.strip().casefold()).strip("-")
        return f"https://www.magazineluiza.com.br/busca/{quote(slug)}/"

    def _offers_from_magalu_rows(
        self, rows: list[dict[str, object]], collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        for row in rows:
            seller_id = _magalu_seller_id(row.get("url"))
            row["seller_external_id"] = seller_id
            row["seller_kind"] = (
                _magalu_party_kind(seller_id).value if seller_id else None
            )
            row["condition"] = _magalu_card_condition(row.get("title"))
        prepared = _apply_installment_summary(
            rows, text_key="installmentText", total_key="installmentTotal"
        )
        return self.offers_from_rows(prepared, collected_at)

    async def _collect_once(self, request: CollectionRequest) -> CollectionResult:
        """Adquire via CDP e entrega ao parser SSR, sem fallback operacional."""
        started_at = self._clock()
        html = await self._search_transport.fetch_html(
            self.build_url(request.search_query)
        )
        rows = _magalu_rows_from_next_data(html)
        offers = self._offers_from_magalu_rows(rows, self._clock())
        return CollectionResult(self.source_code, started_at, self._clock(), offers)

    async def extract(
        self, page: Page, collected_at: datetime
    ) -> tuple[RawCollectedOffer, ...]:
        next_data = page.locator("script#__NEXT_DATA__").first
        rows: list[dict[str, object]] = []
        if await next_data.count():
            payload = await next_data.text_content()
            if payload:
                rows = _magalu_rows_from_next_data(
                    f'<script id="__NEXT_DATA__">{payload}</script>'
                )
        if not rows:
            rows = await page.locator("body").evaluate(
                """body => { const cards = [...body.querySelectorAll('a[data-testid="product-card-link"][href*="/p/"]')]; return cards.map(card => { const url = new URL(card.href); const text = card.innerText || ''; const currency = card.querySelector('[data-testid="price-value-currency"]')?.textContent?.trim(); const integer = card.querySelector('[data-testid="price-value-integer"]')?.textContent?.trim(); const fraction = card.querySelector('[data-testid="price-value-split-cents-fraction"]')?.textContent?.trim(); const price = currency && integer && fraction ? `${currency} ${integer},${fraction}` : card.querySelector('[data-testid="product-card-price-final"]')?.textContent; const match = url.pathname.match(/\\/p\\/([^/]+)/); const sellerId = url.searchParams.get('seller_id'); const tags = card.querySelector('[data-testid="product-card-tags"]')?.textContent || ''; const rating = card.querySelector('[data-testid="rating-score"]')?.textContent; const reviewCount = card.querySelector('[data-testid="rating-label"]')?.textContent?.replace(/[()]/g, '').trim(); const hasRating = !!rating && !!reviewCount; return {url: card.href, title: card.querySelector('[data-testid="product-card-title"]')?.textContent, price, external_id: match?.[1], seller_external_id: sellerId, seller: sellerId === 'magazineluiza' ? 'Magalu' : sellerId, image: card.querySelector('[data-testid="product-card-media"]')?.currentSrc || card.querySelector('[data-testid="product-card-media"]')?.src, shipping: /frete gr[aá]tis/i.test(tags) ? 'Frete grátis' : null, availability: null, evidence: text, rating_average: hasRating ? rating : null, review_count: hasRating ? reviewCount : null, installmentText: card.querySelector('[data-testid="product-card-price-installment"]')?.textContent}; }); }"""
            )
        return self._offers_from_magalu_rows(rows, collected_at)

    async def resolve_marketplace_parties(
        self, page: Page
    ) -> tuple[MarketplacePartyKind, MarketplacePartyKind]:
        seller, fulfillment = _magalu_parties_from_text(
            await page.locator("body").inner_text()
        )
        return _magalu_party_kind(seller), _magalu_party_kind(fulfillment)

    async def resolve_seller_name(self, page: Page) -> str | None:
        seller, _fulfillment = _magalu_parties_from_text(
            await page.locator("body").inner_text()
        )
        return seller

    async def resolve_offer_condition(self, page: Page) -> str | None:
        documents = await page.locator(
            'script[type="application/ld+json"]'
        ).all_text_contents()
        mapping = {
            "newcondition": "Novo",
            "usedcondition": "Usado",
            "refurbishedcondition": "Recondicionado",
        }
        for raw_json in documents:
            try:
                document = json.loads(raw_json)
            except TypeError, ValueError:
                continue
            if not isinstance(document, dict):
                continue
            value = str(document.get("itemCondition") or "").rsplit("/", 1)[-1]
            if value.casefold() in mapping:
                return mapping[value.casefold()]
        return None

    async def resolve_offer_availability(self, page: Page) -> str | None:
        documents = await page.locator(
            'script[type="application/ld+json"]'
        ).all_text_contents()
        for raw_json in documents:
            try:
                document = json.loads(raw_json)
            except TypeError, ValueError:
                continue
            if not isinstance(document, dict):
                continue
            offers = document.get("offers")
            if not isinstance(offers, dict):
                continue
            value = str(offers.get("availability") or "").rsplit("/", 1)[-1]
            if value.casefold() in {"instock", "limitedavailability"}:
                return "Disponível"
            if value.casefold() in {"outofstock", "soldout", "discontinued"}:
                return "Indisponível"
        return None
