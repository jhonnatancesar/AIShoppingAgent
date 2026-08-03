from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.collection import (
    Availability,
    CollectionNormalizationError,
    CollectionResult,
    PriceNormalizer,
    RawCollectedOffer,
)

NOW = datetime(2026, 8, 2, 20, tzinfo=UTC)


def raw_offer(**changes) -> RawCollectedOffer:
    values = {
        "source_code": "amazon",
        "url": "https://example.test/product",
        "title": "GPU",
        "collected_at": NOW,
        "external_id": "sku-1",
        "seller_external_id": "seller-1",
        "seller_name": "Loja Parceira",
        "raw_price": "R$ 4.999,90",
        "raw_currency": "BRL",
        "raw_shipping": "R$ 20,10",
        "raw_availability": "Em estoque",
        "raw_fulfillment": "Entregue pela Amazon",
        "evidence": {"card_text": "sanitized"},
    }
    values.update(changes)
    return RawCollectedOffer(**values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("R$ 4.999,90", Decimal("4999.90")),
        ("R$\xa09,324.42", Decimal("9324.42")),
        ("R$ 1.234", Decimal("1234")),
        ("R$ 1234", Decimal("1234")),
        ("R$ 0,1234", Decimal("0.1234")),
    ),
)
def test_normalizes_exact_brl_formats_seen_in_selected_sources(raw, expected) -> None:
    normalized = PriceNormalizer().normalize_offer(raw_offer(raw_price=raw))

    assert normalized.amount == expected
    assert isinstance(normalized.amount, Decimal)
    assert normalized.currency == "BRL"


def test_separates_shipping_and_calculates_exact_total() -> None:
    normalized = PriceNormalizer().normalize_offer(raw_offer())

    assert normalized.amount == Decimal("4999.90")
    assert normalized.shipping_amount == Decimal("20.10")
    assert normalized.total_amount == Decimal("5020.00")
    assert normalized.availability is Availability.AVAILABLE


def test_free_and_unknown_shipping_have_distinct_meaning() -> None:
    free = PriceNormalizer().normalize_offer(raw_offer(raw_shipping="Frete grátis"))
    unknown = PriceNormalizer().normalize_offer(
        raw_offer(raw_shipping="Calcule o frete")
    )

    assert free.shipping_amount == Decimal(0)
    assert free.total_amount == free.amount
    assert unknown.shipping_amount is None
    assert unknown.total_amount == unknown.amount


def test_preserves_marketplace_seller_fulfillment_and_evidence() -> None:
    original = raw_offer()
    normalized = PriceNormalizer().normalize_offer(original)

    assert normalized.raw_offer is original
    assert normalized.seller_external_id == "seller-1"
    assert normalized.seller_name == "Loja Parceira"
    assert normalized.fulfillment == "Entregue pela Amazon"
    assert normalized.raw_offer.evidence == {"card_text": "sanitized"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("Indisponível", Availability.UNAVAILABLE),
        ("Temporariamente fora de estoque", Availability.UNAVAILABLE),
        ("Disponível", Availability.AVAILABLE),
        (None, Availability.UNKNOWN),
        ("Consulte", Availability.UNKNOWN),
    ),
)
def test_normalizes_availability_without_inventing_state(raw, expected) -> None:
    normalized = PriceNormalizer().normalize_offer(raw_offer(raw_availability=raw))
    assert normalized.availability is expected


def test_normalizes_complete_collection_result_in_order() -> None:
    raw_result = CollectionResult(
        "amazon",
        NOW,
        NOW + timedelta(seconds=2),
        (raw_offer(), raw_offer(external_id="sku-2", raw_price="R$ 99,90")),
    )

    result = PriceNormalizer().normalize_result(raw_result)

    assert result.raw_result is raw_result
    assert tuple(offer.amount for offer in result.offers) == (
        Decimal("4999.90"),
        Decimal("99.90"),
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"raw_price": None},
        {"raw_price": "sob consulta"},
        {"raw_price": "R$ 1,2,3"},
        {"raw_price": "R$ 1,12345"},
        {"raw_price": "R$ 12345678901234567890"},
        {
            "raw_price": "R$ 999999999999999.9999",
            "raw_shipping": "R$ 0,0001",
        },
        {"raw_price": "R$ 10,00", "raw_currency": "USD"},
        {"raw_price": "10,00", "raw_currency": None},
        {"raw_price": "R$ 10,00", "raw_currency": "reais"},
        {"raw_shipping": "US$ 5.00"},
    ),
)
def test_rejects_missing_ambiguous_or_inconsistent_money(changes) -> None:
    with pytest.raises(CollectionNormalizationError):
        PriceNormalizer().normalize_offer(raw_offer(**changes))
