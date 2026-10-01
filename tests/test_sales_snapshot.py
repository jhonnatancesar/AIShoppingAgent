"""TASK-136 (passo 1) -- vendas publicadas pela loja no card, lidas antes da IA.

O GG só LÊ o número que a loja publica (Amazon: comprados no mês passado;
Mercado Livre: vendidos). Os textos abaixo seguem o formato conhecido dessas lojas;
o formato real ao vivo precisa ser conferido numa coleta de verdade."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.collection.contracts import RawCollectedOffer
from app.collection.normalization import (
    SALES_SCOPE_LAST_MONTH,
    SALES_SCOPE_TOTAL,
    PriceNormalizer,
    parse_sales_snapshot,
)
from app.collection.orchestration import _apply_rating_snapshot
from app.offers.models import Offer

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mais de 1 mil comprados no mês passado", (1000, SALES_SCOPE_LAST_MONTH)),
        (
            "+ de 2,5 mil comprados no mês passado",
            (2500, SALES_SCOPE_LAST_MONTH),
        ),  # "+ de" não é um padrão da loja
        ("2,5 mil comprados no mês passado", (2500, SALES_SCOPE_LAST_MONTH)),
        ("100+ comprados no mês passado", (100, SALES_SCOPE_LAST_MONTH)),
        ("50 compras no mês passado", (50, SALES_SCOPE_LAST_MONTH)),
        ("+500 vendidos", (500, SALES_SCOPE_TOTAL)),
        ("+5mil vendidos", (5000, SALES_SCOPE_TOTAL)),
        ("Mais de 1000 vendidos", (1000, SALES_SCOPE_TOTAL)),
        ("1.500 vendidos", (1500, SALES_SCOPE_TOTAL)),
        (
            "Placa-mãe B550M\n4,8 (320)\n+100 vendidos\nR$ 599,90",
            (100, SALES_SCOPE_TOTAL),
        ),
    ],
)
def test_sales_text_is_read_with_its_scope(text, expected) -> None:
    assert parse_sales_snapshot(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "Vendido por Loja X",
        "Vendido e entregue por Amazon",
        "R$ 599,90 em 10x",
        "Frete grátis",
        "0 vendidos",
        "Esgotado",
    ],
)
def test_text_without_a_clear_sales_number_is_never_a_sale(text) -> None:
    assert parse_sales_snapshot(text) is None


def _raw(card_text: str | None) -> RawCollectedOffer:
    return RawCollectedOffer(
        source_code="amazon",
        url="https://amazon.example.test/dp/B0",
        title="Placa-mãe B550M",
        collected_at=NOW,
        external_id="B0",
        raw_price="599.90",
        raw_currency="BRL",
        raw_availability="Disponível",
        evidence={"card_text": card_text} if card_text is not None else {},
    )


def test_normalized_offer_exposes_the_sales_from_the_card_text() -> None:
    item = PriceNormalizer().normalize_offer(
        _raw("Placa-mãe B550M\nMais de 1 mil comprados no mês passado")
    )
    assert item.sales == (1000, SALES_SCOPE_LAST_MONTH)
    assert PriceNormalizer().normalize_offer(_raw(None)).sales is None


def test_snapshot_is_applied_and_absence_never_erases_the_last_value() -> None:
    offer = Offer()
    with_sales = PriceNormalizer().normalize_offer(_raw("+500 vendidos"))
    _apply_rating_snapshot(offer, with_sales)
    assert (offer.sales_count, offer.sales_scope) == (500, SALES_SCOPE_TOTAL)
    assert offer.sales_observed_at == NOW

    without = PriceNormalizer().normalize_offer(_raw("sem número aqui"))
    _apply_rating_snapshot(offer, without)
    assert offer.sales_count == 500, "ausência nunca apaga o último snapshot"


def test_items_without_a_sales_attribute_keep_working() -> None:
    offer = Offer()
    legacy = SimpleNamespace(
        rating_average=None,
        review_count=None,
        raw_offer=SimpleNamespace(collected_at=NOW),
    )
    _apply_rating_snapshot(offer, legacy)
    assert offer.sales_count is None


# --- textos REAIS vistos nas lojas em 2026-10-01 (validação ao vivo) ---------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Amazon: card da busca (evidência real)
        (
            "4,7 de 5 estrelas (2,8 mil) Mais de 300 compras no mês passado Preço, "
            "página do produto R$ 799,00",
            (300, SALES_SCOPE_LAST_MONTH),
        ),
        (
            "4,0 de 5 estrelas (98) Mais de 50 compras no mês passado R$ 388,55",
            (50, SALES_SCOPE_LAST_MONTH),
        ),
        # Mercado Livre: subtítulo logo acima do título na página do produto
        ("Novo  |  +1000 vendidos", (1000, SALES_SCOPE_TOTAL)),
        ("Novo  |  +10 mil vendidos", (10000, SALES_SCOPE_TOTAL)),
    ],
)
def test_real_store_texts(text, expected) -> None:
    assert parse_sales_snapshot(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # Kit da Amazon sem avaliação nem venda; Terabyte/Pichau/Magalu não mostram vendas
        "KIT PROCESSADOR AMD RYZEN 5 5600GT + PLACA MÃE GIGABYTE B550M R$ 2.944,05 "
        "Entrega GRÁTIS Somente 4 em estoque Adicionar ao carrinho",
        "Frete grátis Placa Mãe MAXSUN B550M ★ ★ ★ ★ ★ 4.9 (84) De: R$ 635,28",
        "Frete Grátis: Sul e Sudeste Placa Mae MSI R$ 914.99 À vista 15% de desconto no PIX",
        # elementos do ML que NÃO são a venda do produto
        "Mais vendidos",
        "MAIS VENDIDO",
    ],
)
def test_real_texts_without_sales_are_never_a_sale(text) -> None:
    assert parse_sales_snapshot(text) is None


def test_detail_sales_text_is_read_when_the_card_has_none() -> None:
    raw = RawCollectedOffer(
        source_code="mercadolivre",
        url="https://www.mercadolivre.com.br/p/MLB1",
        title="Placa",
        collected_at=NOW,
        external_id="MLB1",
        raw_price="R$ 700,00",
        raw_currency="BRL",
        evidence={
            "card_text": "Placa 4.8 R$ 700",
            "detail_sales_text": "Novo  |  +500 vendidos",
        },
    )
    assert PriceNormalizer().normalize_offer(raw).sales == (500, SALES_SCOPE_TOTAL)
