"""TASK-136 (passo 2) -- ranking por popularidade e escolha dos candidatos da IA."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.collection.contracts import RawCollectedOffer
from app.collection.funnel import (
    AI_COUNT,
    POOL_SIZE,
    popularity_points,
    select_for_ai,
)
from app.collection.normalization import Availability, OfferCondition, PriceNormalizer

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def _item(
    name: str,
    price: str,
    *,
    rating: str | None = None,
    reviews: int | None = None,
    sales_text: str | None = None,
    availability: str = "disponível",
    condition: str | None = None,
    store: str = "amazon",
):
    raw = RawCollectedOffer(
        source_code=store,
        url=f"https://{store}.example.test/{name}",
        title=f"Placa {name}",
        collected_at=NOW,
        external_id=name,
        raw_price=price,
        raw_currency="BRL",
        raw_availability=availability,
        raw_condition=condition,
        raw_rating_average=rating,
        raw_review_count=str(reviews) if reviews is not None else None,
        evidence={"card_text": sales_text or ""},
    )
    return PriceNormalizer().normalize_offer(raw)


def _names(items) -> list[str]:
    return [item.raw_offer.external_id for item in items]


def test_three_five_star_reviews_never_beat_160_reviews_of_4_9() -> None:
    kabum = _item("kabum", "500", rating="5", reviews=3, store="kabum")
    amazon = _item("amazon", "600", rating="4.9", reviews=160)
    assert popularity_points(kabum) == Decimal("15")
    assert popularity_points(amazon) == Decimal("784.0")
    assert _names(select_for_ai([kabum, amazon]).pool) == ["amazon", "kabum"]


def test_stars_and_people_count_together() -> None:
    low = _item("low", "500", rating="4.0", reviews=1000)
    high = _item("high", "500", rating="4.9", reviews=900)
    assert _names(select_for_ai([low, high]).pool) == ["high", "low"]


def test_sales_alone_rank_above_no_signal_but_below_any_rating() -> None:
    sales_only = _item("ml", "500", sales_text="+500 vendidos", store="mercadolivre")
    nothing = _item("nothing", "100")
    rated = _item("rated", "900", rating="4.0", reviews=2)
    assert popularity_points(sales_only) == Decimal(0)
    assert _names(select_for_ai([nothing, sales_only, rated]).pool) == [
        "rated",
        "ml",
        "nothing",
    ]


def test_sales_only_break_ties_when_there_are_reviews() -> None:
    a = _item("a", "500", rating="4.5", reviews=100, sales_text="+100 vendidos")
    b = _item("b", "500", rating="4.5", reviews=100, sales_text="+1000 vendidos")
    assert _names(select_for_ai([a, b]).pool) == ["b", "a"]


def test_offers_without_any_signal_rank_last() -> None:
    nothing = _item("nothing", "100")
    some = _item("some", "900", rating="4.0", reviews=2)
    assert _names(select_for_ai([nothing, some]).pool) == ["some", "nothing"]


def test_pool_is_the_ten_most_popular_and_chosen_are_the_two_cheapest_of_them() -> None:
    popular = [
        _item(f"p{n}", str(1000 + n * 10), rating="4.8", reviews=500 - n)
        for n in range(10)
    ]
    cheap_unknown = _item("cheap", "50")  # baratíssima, mas sem popularidade
    selection = select_for_ai([cheap_unknown, *popular])
    assert len(selection.pool) == POOL_SIZE == 10
    assert "cheap" not in _names(selection.pool)
    assert _names(selection.chosen) == ["p0", "p1"]
    assert len(selection.chosen) == AI_COUNT == 2


def test_unavailable_offers_never_compete() -> None:
    gone = _item("gone", "100", rating="5", reviews=5000, availability="Esgotado")
    ok = _item("ok", "900", rating="4.0", reviews=10)
    selection = select_for_ai([gone, ok])
    assert _names(selection.pool) == ["ok"]
    assert _names(selection.chosen) == ["ok"]


def test_new_condition_is_chosen_before_a_cheaper_used_one() -> None:
    used = _item("used", "300", rating="4.9", reviews=100, condition="Usado")
    new = _item("new", "800", rating="4.9", reviews=100, condition="Novo")
    assert used.condition is OfferCondition.USED
    assert _names(select_for_ai([used, new], ai_count=1).chosen) == ["new"]


def test_selection_is_deterministic_and_handles_small_inputs() -> None:
    items = [_item(f"i{n}", "700", rating="4.5", reviews=50) for n in range(4)]
    first = select_for_ai(items)
    second = select_for_ai(list(reversed(items)))
    assert _names(first.pool) == _names(second.pool)
    assert _names(first.chosen) == _names(second.chosen)
    assert select_for_ai([]).chosen == ()
    assert len(select_for_ai(items[:1]).chosen) == 1


def test_invalid_sizes_are_rejected() -> None:
    with pytest.raises(ValueError):
        select_for_ai([], pool_size=0)
    with pytest.raises(ValueError):
        select_for_ai([], ai_count=0)


def test_availability_enum_is_the_one_the_funnel_reads() -> None:
    assert _item("x", "100").availability is Availability.AVAILABLE
