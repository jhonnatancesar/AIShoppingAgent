"""TASK-089 (DEC-069): captura de opções de parcelamento por provider.

Cobre exatamente os quatro cenários confirmados na investigação real:
Pichau (card + tabela individual com desconto variável por faixa e por
produto), Terabyte (card + painel expandido 1x-18x com juros a partir de
certa faixa), Amazon e KaBuM! (só o que já vem no card, nenhuma tabela
individual real existe -- nunca inventa opções intermediárias)."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from app.collection import (
    BrowserSession,
    InstallmentInterestKind,
    KabumProvider,
    PichauProvider,
    RawInstallmentOption,
    TerabyteProvider,
)
from app.collection.providers.base import _merge_installment_options
from app.collection.providers.stores import (
    AmazonProvider,
    _parse_pichau_installment_row,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def _extract(provider_type, html):
    async def scenario():
        async with BrowserSession() as session:
            page = await session.new_page()
            await page.set_content(html)
            return await provider_type().extract(page, NOW)

    return asyncio.run(scenario())


# --- Pichau: card ---


def test_pichau_card_captures_default_installment_with_total() -> None:
    html = (
        '<a data-cy="list-product" href="https://www.pichau.com.br/gpu-x">'
        "<h2>GPU Pichau</h2>"
        '<div class="mui-12athy2-price_vista">R$&nbsp;4.299,99</div>'
        '<span class="mui-1ww3op6-price_vista_text">À vista</span>'
        '<span class="mui-1r5paeb-price_vista_additional">15% de desconto no PIX</span>'
        '<div class="mui-10zdolh-price_total">R$&nbsp;5.058,81</div>'
        '<p class="mui-eg1t7a-price_parcelado_text">Em até 12x de'
        '<span class="mui-1398tt3-price_parcelado_inline"> R$&nbsp;421,57</span> '
        '<span class="mui-eg1t7a-price_parcelado_text">Sem juros no cartão</span></p>'
        "</a>"
    )
    offers = _extract(PichauProvider, html)

    assert len(offers) == 1
    options = offers[0].installment_options
    assert len(options) == 1
    assert options[0].installment_count == 12
    assert "421,57" in options[0].raw_amount
    assert options[0].raw_total_amount is not None
    assert "5.058,81" in options[0].raw_total_amount
    assert options[0].interest_kind is InstallmentInterestKind.INTEREST_FREE


def test_pichau_card_without_parcelado_block_has_no_installment_options() -> None:
    html = (
        '<a data-cy="list-product" href="https://www.pichau.com.br/gpu-y">'
        "<h2>GPU Pichau sem parcelamento</h2>"
        '<div class="price_vista">R$ 199,90</div>'
        "</a>"
    )
    offers = _extract(PichauProvider, html)

    assert len(offers) == 1
    assert offers[0].installment_options == ()


# --- Pichau: página individual (tabela PARCELAMENTO) ---


def test_pichau_installment_row_parses_discount_and_interest_free() -> None:
    """Mesmo produto que a investigação real encontrou (RTX 5050 sem selo
    especial): desconto variável por faixa, nunca fixo em 15%."""
    discounted = _parse_pichau_installment_row("1x de R$2.011,75 (com 10% de desconto)")
    assert discounted is not None
    assert discounted.installment_count == 1
    assert discounted.discount_percent == Decimal("10")
    assert discounted.interest_kind is InstallmentInterestKind.UNKNOWN

    smaller_discount = _parse_pichau_installment_row(
        "4x de R$542,06 (com 3% de desconto)"
    )
    assert smaller_discount is not None
    assert smaller_discount.discount_percent == Decimal("3")

    interest_free = _parse_pichau_installment_row("7x de R$319,33 (sem juros)")
    assert interest_free is not None
    assert interest_free.discount_percent is None
    assert interest_free.interest_kind is InstallmentInterestKind.INTEREST_FREE


def test_pichau_installment_row_rejects_unparseable_text() -> None:
    assert _parse_pichau_installment_row("Frete grátis para todo o Brasil") is None
    assert _parse_pichau_installment_row("") is None


async def _resolve_installment_options(provider, html) -> tuple:
    async with BrowserSession() as session:
        page = await session.new_page()
        await page.set_content(html)
        return await provider.resolve_installment_options(page)


def test_pichau_resolve_installment_options_reads_full_table_from_page() -> None:
    """Reproduz literalmente a tabela real encontrada (Palit RTX 5050,
    sem o selo "15% em até 6x"): desconto decrescente até 6x, depois
    "sem juros" -- nunca um percentual hardcoded no parser."""
    html = (
        '<div class="mui-x3kldx-installmentsWrapper">'
        '<div class="mui-1oz0vcv-installment">1x de R$2.011,75 (com 10% de desconto)</div>'
        '<div class="mui-1oz0vcv-installment">2x de R$1.061,76 (com 5% de desconto)</div>'
        '<div class="mui-1oz0vcv-installment">6x de R$361,37 (com 3% de desconto)</div>'
        '<div class="mui-1oz0vcv-installment">7x de R$319,33 (sem juros)</div>'
        '<div class="mui-1oz0vcv-installment">12x de R$186,27 (sem juros)</div>'
        "</div>"
    )
    options = asyncio.run(_resolve_installment_options(PichauProvider(), html))

    by_count = {option.installment_count: option for option in options}
    assert set(by_count) == {1, 2, 6, 7, 12}
    assert by_count[1].discount_percent == Decimal("10")
    assert by_count[2].discount_percent == Decimal("5")
    assert by_count[6].discount_percent == Decimal("3")
    assert by_count[7].interest_kind is InstallmentInterestKind.INTEREST_FREE
    assert by_count[7].discount_percent is None
    assert by_count[12].interest_kind is InstallmentInterestKind.INTEREST_FREE


def test_pichau_resolve_installment_options_empty_when_no_table() -> None:
    options = asyncio.run(
        _resolve_installment_options(PichauProvider(), "<div>sem parcelamento</div>")
    )
    assert options == ()


# --- Terabyte: card ---


def test_terabyte_card_captures_installment_without_total() -> None:
    html = (
        '<div class="product-item">'
        '<a class="product-item__name" href="https://www.terabyteshop.com.br/produto/1/gpu">GPU</a>'
        '<div class="product-item__new-price"><span>R$ 19.149,99</span> à vista no Pix</div>'
        '<div class="product-item__juros"><span>12x</span><small> de </small>'
        "<span>R$ 1.877,45</span><small> sem juros</small><small> no cartão</small></div>"
        "</div>"
    )
    offers = _extract(TerabyteProvider, html)

    assert len(offers) == 1
    options = offers[0].installment_options
    assert len(options) == 1
    assert options[0].installment_count == 12
    assert options[0].raw_total_amount is None  # Terabyte nunca rotula total
    assert options[0].interest_kind is InstallmentInterestKind.INTEREST_FREE


# --- Terabyte: painel expandido "VER PARCELAMENTO" ---


def test_terabyte_resolve_installment_options_reads_full_range_with_interest_tiers() -> (
    None
):
    """Reproduz literalmente o painel real encontrado: 1x-3x com desconto,
    4x-12x sem juros, 13x+ com juros -- faixas descobertas na investigação,
    nunca hardcoded no parser (o teste usa valores DIFERENTES dos originais
    para provar que o parser lê o texto, não uma tabela fixa)."""
    html = (
        '<div id="detalheparcelamento">'
        "Crédito - em até 12x sem juros ou em até 18x com juros"
        "1x de R$ 9.000,00 c/desconto de 20%*"
        "2x de R$ 4.700,00 c/desconto de 8%*"
        "6x de R$ 1.550,00 s/juros*"
        "12x de R$ 800,00 s/juros*"
        "13x de R$ 780,00 c/juros*"
        "18x de R$ 600,00 c/juros*"
        "* Para pagamentos no cartão de crédito"
        "</div>"
    )
    options = asyncio.run(_resolve_installment_options(TerabyteProvider(), html))

    by_count = {option.installment_count: option for option in options}
    assert set(by_count) == {1, 2, 6, 12, 13, 18}
    assert by_count[1].discount_percent == Decimal("20")
    assert by_count[1].interest_kind is InstallmentInterestKind.UNKNOWN
    assert by_count[2].discount_percent == Decimal("8")
    assert by_count[6].interest_kind is InstallmentInterestKind.INTEREST_FREE
    assert by_count[6].discount_percent is None
    assert by_count[13].interest_kind is InstallmentInterestKind.WITH_INTEREST
    assert by_count[18].interest_kind is InstallmentInterestKind.WITH_INTEREST
    # TASK-089: nenhuma opção carrega total explícito -- a página nunca mostra um
    assert all(option.raw_total_amount is None for option in options)


def test_terabyte_resolve_installment_options_ignores_free_shipping_suffix() -> None:
    """ "+ Frete Grátis" (visto em algumas faixas reais) não é parcelamento
    -- não pode virar campo nem quebrar o parsing da linha."""
    html = (
        '<div id="detalheparcelamento">4x de R$ 1.000,00 s/juros + Frete Grátis*</div>'
    )
    options = asyncio.run(_resolve_installment_options(TerabyteProvider(), html))

    assert len(options) == 1
    assert options[0].installment_count == 4
    assert options[0].interest_kind is InstallmentInterestKind.INTEREST_FREE


def test_terabyte_resolve_installment_options_empty_when_panel_absent() -> None:
    options = asyncio.run(
        _resolve_installment_options(TerabyteProvider(), "<div>sem painel</div>")
    )
    assert options == ()


# --- Amazon: só o card, contagem variável, nunca tabela individual ---


def test_amazon_card_captures_variable_installment_count() -> None:
    html = (
        '<div data-component-type="s-search-result" data-asin="B0123">'
        '<h2><a href="https://www.amazon.com.br/dp/B0123">GPU Amazon</a></h2>'
        '<span class="a-price"><span class="a-offscreen">R$ 3.210,05</span></span>'
        '<div class="a-row a-size-base a-color-base">'
        "à vista no Pix ou NuPay ou em até 10x de "
        '<span class="a-price"><span class="a-offscreen">R$ 259,90</span></span> sem juros'
        "</div>"
        "<button>Adicionar ao carrinho</button>"
        "</div>"
    )
    offers = _extract(AmazonProvider, html)

    assert len(offers) == 1
    options = offers[0].installment_options
    assert len(options) == 1
    assert options[0].installment_count == 10
    assert options[0].raw_total_amount is None
    assert options[0].interest_kind is InstallmentInterestKind.INTEREST_FREE


def test_amazon_never_visits_individual_page_for_installments() -> None:
    """Amazon não implementa `resolve_installment_options` -- a
    investigação real não achou tabela nenhuma; nenhuma navegação extra
    pode ser disparada procurando por ela."""
    from app.collection.providers.base import PlaywrightStoreProvider

    assert (
        AmazonProvider.resolve_installment_options
        is PlaywrightStoreProvider.resolve_installment_options
    )


# --- KaBuM!: só o card, sem menção a juros, NuPay nunca vira opção ---


def test_kabum_card_captures_installment_without_interest_claim() -> None:
    html = (
        "<main>"
        '<a href="https://www.kabum.com.br/produto/456/gpu">'
        '<span class="line-clamp-2">GPU Kabum</span>'
        '<span class="text-base font-semibold">R$</span>'
        '<span class="text-base font-semibold">4.799,99</span>'
        '<span class="text-xs text-gray-400">No PIX ou 10x de R$ 564,70</span>'
        "</a></main>"
    )
    offers = _extract(KabumProvider, html)

    assert len(offers) == 1
    options = offers[0].installment_options
    assert len(options) == 1
    assert options[0].installment_count == 10
    # TASK-089: KaBuM! nunca declara "sem juros" no card -- nunca assumido.
    assert options[0].interest_kind is InstallmentInterestKind.UNKNOWN
    assert options[0].discount_percent is None
    assert options[0].raw_total_amount is None


def test_kabum_nupay_mention_never_becomes_a_fake_option() -> None:
    """ "até 36x no NuPay" não vem acompanhado de valor de parcela --
    nunca pode virar uma opção inventada de 36x."""
    html = (
        "<main>"
        '<a href="https://www.kabum.com.br/produto/700804/gpu">'
        '<span class="line-clamp-2">GPU Kabum cara</span>'
        '<span class="text-base font-semibold">R$</span>'
        '<span class="text-base font-semibold">28.999,99</span>'
        '<span class="text-xs text-gray-400">'
        "10x R$ 3.411,76 s/ juros ou em até 36x no NuPay"
        "</span>"
        "</a></main>"
    )
    offers = _extract(KabumProvider, html)

    assert len(offers) == 1
    options = offers[0].installment_options
    counts = {option.installment_count for option in options}
    assert 36 not in counts


def test_kabum_never_visits_individual_page_for_installments() -> None:
    from app.collection.providers.base import PlaywrightStoreProvider

    assert (
        KabumProvider.resolve_installment_options
        is PlaywrightStoreProvider.resolve_installment_options
    )


# --- merge card + página individual (Pichau/Terabyte) ---


def test_merge_installment_options_page_enriches_without_losing_card_total() -> None:
    """A página individual não repete o total que só o card mostra -- o
    merge precisa preservar esse total ao mesmo tempo em que absorve o
    desconto/juros mais detalhado da página para a MESMA quantidade."""
    card = (
        RawInstallmentOption(
            installment_count=12,
            raw_amount="R$ 421,57",
            raw_total_amount="R$ 5.058,81",
            interest_kind=InstallmentInterestKind.INTEREST_FREE,
        ),
    )
    page = (
        RawInstallmentOption(installment_count=1, raw_amount="R$ 4.299,99"),
        RawInstallmentOption(
            installment_count=12,
            raw_amount="R$ 421,57",
            interest_kind=InstallmentInterestKind.INTEREST_FREE,
        ),
    )

    merged = _merge_installment_options(card, page)

    by_count = {option.installment_count: option for option in merged}
    assert set(by_count) == {1, 12}
    assert by_count[12].raw_total_amount == "R$ 5.058,81"  # preservado do card
    assert by_count[1].raw_total_amount is None


def test_merge_installment_options_never_duplicates_count() -> None:
    card = (RawInstallmentOption(installment_count=12, raw_amount="R$ 1,00"),)
    page = (
        RawInstallmentOption(installment_count=12, raw_amount="R$ 2,00"),
        RawInstallmentOption(installment_count=12, raw_amount="R$ 3,00"),
    )

    merged = _merge_installment_options(card, page)

    assert len(merged) == 1
    assert merged[0].installment_count == 12


# ---------------------------------------------------------------------------
# Auditoria pós-implementação: prova explícita de que discount_percent e
# interest_kind nunca são calculados/inferidos por comparação matemática --
# só extraídos quando o texto real da loja declara o dado explicitamente.
# ---------------------------------------------------------------------------


def test_pichau_discount_percent_absent_without_explicit_percent_text_even_when_math_would_suggest_one() -> (
    None
):
    """12x de R$500,00 (sem juros) -- sem "% de desconto" no texto, mesmo
    que R$500,00 seja um valor "redondo"/"parece com desconto" comparado a
    qualquer outro preço, discount_percent tem que ficar None. O parser
    não tem acesso a nenhum "preço de referência" para calcular nada aqui
    -- só o texto desta única linha."""
    option = _parse_pichau_installment_row("12x de R$500,00 (sem juros)")
    assert option is not None
    assert option.discount_percent is None


def test_pichau_discount_percent_reads_literal_number_never_recomputes_it() -> None:
    """O percentual final é exatamente o dígito que aparece no texto -- se
    a loja escrever "37%" (um número que não corresponde a nenhuma conta
    "bonita" com o valor da parcela), o parser tem que devolver 37, prova
    de que é leitura literal, não um cálculo por trás."""
    option = _parse_pichau_installment_row("1x de R$1.234,56 (com 37% de desconto)")
    assert option is not None
    assert option.discount_percent == Decimal("37")


def test_pichau_interest_kind_unknown_without_explicit_juros_text_regardless_of_amount() -> (
    None
):
    """Sem "sem juros" nem "com juros" no texto, fica UNKNOWN -- mesmo com
    desconto declarado (que é uma informação independente de juros)."""
    option = _parse_pichau_installment_row("3x de R$999,99 (com 8% de desconto)")
    assert option is not None
    assert option.interest_kind is InstallmentInterestKind.UNKNOWN


def test_terabyte_interest_kind_never_derived_from_installment_total_comparison() -> (
    None
):
    """Constrói deliberadamente uma linha "c/juros" cujo valor de parcela,
    multiplicado pela contagem, dá um total MENOR que uma opção "s/juros"
    anterior -- se o parser estivesse comparando totais para decidir
    juros, ele erraria aqui. Confirma que a classificação vem só do
    marcador literal "c/juros"/"s/juros" no texto, nunca de conta."""
    html = (
        '<div id="detalheparcelamento">'
        "6x de R$ 1.000,00 s/juros*"  # total "matemático" = 6.000,00
        "3x de R$ 500,00 c/juros*"  # total "matemático" = 1.500,00 (menor!)
        "</div>"
    )
    options = asyncio.run(_resolve_installment_options(TerabyteProvider(), html))
    by_count = {option.installment_count: option for option in options}
    assert by_count[6].interest_kind is InstallmentInterestKind.INTEREST_FREE
    assert by_count[3].interest_kind is InstallmentInterestKind.WITH_INTEREST


def test_terabyte_discount_percent_reads_literal_digit_not_derived_from_price_diff() -> (
    None
):
    """Percentual de desconto igual ao dígito do texto, mesmo quando ele
    não bate com nenhuma conta óbvia de "preço cheio - preço com
    desconto" (não existe preço de referência nesta linha isolada para
    calcular nada)."""
    html = '<div id="detalheparcelamento">2x de R$ 999,00 c/desconto de 42%*</div>'
    options = asyncio.run(_resolve_installment_options(TerabyteProvider(), html))
    assert options[0].discount_percent == Decimal("42")


def test_card_level_installment_never_sets_discount_percent() -> None:
    """Nenhuma das 4 lojas declara percentual de desconto atrelado ao
    resumo padrão do card (o desconto do PIX é um dado separado, do preço
    à vista) -- `_installment_options_from_row` nunca preenche
    `discount_percent`, mesmo que o texto do card contenha algum número
    seguido de "%"."""
    from app.collection.providers.base import _installment_options_from_row

    row = {
        "installment_count": "12",
        "installment_amount": "R$ 421,57",
        "installment_interest_free": True,
        # texto hipotético com um "15%" solto em outro campo do card --
        # nunca deveria vazar para discount_percent do parcelamento.
        "evidence": "15% de desconto no PIX",
    }
    options = _installment_options_from_row(row)
    assert len(options) == 1
    assert options[0].discount_percent is None
