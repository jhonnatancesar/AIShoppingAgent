"""Aplicabilidade, dedup e cálculo de preço final com cupom -- puro, sem
banco (app.coupons.pricing). Consumo real de cupons (2026-09-06)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.coupons.models import Coupon
from app.coupons.pricing import (
    AppliedCoupon,
    best_applicable_coupon,
    calculate_final_price,
    is_coupon_applicable,
    normalize_offer_url,
)
from app.offers.models import Offer

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
STORE_ID = uuid4()
PRODUCT_ID = uuid4()


def _coupon(**overrides) -> Coupon:
    defaults = dict(
        id=uuid4(),
        store_id=STORE_ID,
        code="CODE10",
        discount_kind="fixed_amount",
        discount_value=Decimal("10.00"),
        minimum_purchase_amount=None,
        maximum_discount_amount=None,
        scope_kind="store_wide",
        scope_reference=None,
        evidence="ev",
        status="active",
        last_seen_at=NOW,
    )
    defaults.update(overrides)
    return Coupon(**defaults)


def _offer(url: str = "https://amazon.com.br/produto/x") -> Offer:
    return Offer(id=uuid4(), product_id=PRODUCT_ID, store_id=STORE_ID, url=url)


# ---------------------------------------------------------------------------
# normalize_offer_url
# ---------------------------------------------------------------------------


def test_normalize_url_treats_http_and_https_as_equal():
    a = normalize_offer_url("http://amazon.com.br/produto/x")
    b = normalize_offer_url("https://amazon.com.br/produto/x")
    assert a == b


def test_normalize_url_strips_www_case_fragment_and_trailing_slash():
    a = normalize_offer_url("https://WWW.Amazon.com.br/produto/x/#reviews")
    b = normalize_offer_url("https://amazon.com.br/produto/x")
    assert a == b


def test_normalize_url_removes_only_known_tracking_params():
    a = normalize_offer_url(
        "https://amazon.com.br/produto/x?utm_source=ig&utm_medium=social&gclid=abc"
    )
    b = normalize_offer_url("https://amazon.com.br/produto/x")
    assert a == b


def test_normalize_url_preserves_params_that_could_identify_product():
    a = normalize_offer_url("https://amazon.com.br/produto/x?sku=123")
    b = normalize_offer_url("https://amazon.com.br/produto/x?sku=456")
    assert a != b  # nunca ignora query que pode identificar variante


def test_normalize_url_empty_string_is_safe():
    assert normalize_offer_url("") == ""


# ---------------------------------------------------------------------------
# is_coupon_applicable
# ---------------------------------------------------------------------------


def test_store_wide_coupon_applies_to_any_offer_of_the_store():
    coupon = _coupon(scope_kind="store_wide", scope_reference=None)
    assert is_coupon_applicable(coupon, _offer()) is True


def test_product_scope_applies_only_with_exact_normalized_url_match():
    coupon = _coupon(
        scope_kind="product",
        scope_reference="https://www.amazon.com.br/produto/x/?utm_source=ig",
    )
    assert is_coupon_applicable(coupon, _offer("https://amazon.com.br/produto/x")) is True
    assert (
        is_coupon_applicable(coupon, _offer("https://amazon.com.br/produto/y")) is False
    )


def test_product_scope_without_reference_never_applies():
    coupon = _coupon(scope_kind="product", scope_reference=None)
    assert is_coupon_applicable(coupon, _offer()) is False


def test_unknown_scope_never_applies_automatically():
    """DEC-093: scope_kind=None fica "possivelmente aplicável", nunca uma
    afirmação -- excluído da avaliação determinística (decisão explícita
    do usuário, 2026-09-06)."""
    coupon = _coupon(scope_kind=None, scope_reference=None)
    assert is_coupon_applicable(coupon, _offer()) is False


def test_category_scope_never_applies_never_produced_by_worker_today():
    coupon = _coupon(scope_kind="category", scope_reference="placas de video")
    assert is_coupon_applicable(coupon, _offer()) is False


def test_expired_coupon_never_applies_even_if_scope_matches():
    coupon = _coupon(scope_kind="store_wide", status="expired")
    assert is_coupon_applicable(coupon, _offer()) is False


# ---------------------------------------------------------------------------
# calculate_final_price
# ---------------------------------------------------------------------------


def test_fixed_amount_discount():
    coupon = _coupon(discount_kind="fixed_amount", discount_value=Decimal("50.00"))
    result = calculate_final_price(Decimal("500.00"), coupon)
    assert result == (Decimal("50.00"), Decimal("450.00"))


def test_percentage_discount():
    coupon = _coupon(discount_kind="percentage", discount_value=Decimal("10"))
    result = calculate_final_price(Decimal("500.00"), coupon)
    assert result == (Decimal("50.00"), Decimal("450.00"))


def test_minimum_purchase_amount_respected():
    coupon = _coupon(
        discount_kind="fixed_amount",
        discount_value=Decimal("50.00"),
        minimum_purchase_amount=Decimal("600.00"),
    )
    assert calculate_final_price(Decimal("500.00"), coupon) is None
    assert calculate_final_price(Decimal("600.00"), coupon) is not None


def test_maximum_discount_amount_caps_percentage_discount():
    coupon = _coupon(
        discount_kind="percentage",
        discount_value=Decimal("50"),
        maximum_discount_amount=Decimal("30.00"),
    )
    result = calculate_final_price(Decimal("500.00"), coupon)
    assert result == (Decimal("30.00"), Decimal("470.00"))


def test_discount_never_makes_price_negative():
    coupon = _coupon(discount_kind="fixed_amount", discount_value=Decimal("999999.00"))
    result = calculate_final_price(Decimal("100.00"), coupon)
    assert result == (Decimal("100.00"), Decimal("0"))


def test_unknown_discount_kind_is_never_calculated():
    coupon = _coupon(discount_kind="mystery_kind", discount_value=Decimal("10"))
    assert calculate_final_price(Decimal("500.00"), coupon) is None


def test_missing_discount_value_is_never_calculated():
    coupon = _coupon(discount_kind="fixed_amount", discount_value=None)
    assert calculate_final_price(Decimal("500.00"), coupon) is None


# ---------------------------------------------------------------------------
# Deduplicação lógica -- correção 2026-09-06: aplicabilidade por evidência
# -> preço final de TODAS as evidências aplicáveis -> só ENTÃO deduplicar
# (pelo melhor resultado, nunca pela linha mais recente). Testado sempre
# via `best_applicable_coupon` (o fluxo real), nunca uma função de dedup
# isolada -- ela só existe hoje como passo interno.
# ---------------------------------------------------------------------------


def test_same_code_two_applicable_evidences_different_discounts_lower_final_wins():
    """Cenário 6 (auditoria 2026-09-06): mesmo código, duas evidências
    aplicáveis, descontos diferentes -- AMBAS são calculadas, vence o
    menor preço final (maior desconto), não a mais recente."""
    offer = _offer()
    worse = _coupon(
        code="SAME10", evidence="ev-a", discount_value=Decimal("10.00"), last_seen_at=NOW
    )
    better = _coupon(
        code="SAME10", evidence="ev-b", discount_value=Decimal("80.00"), last_seen_at=NOW
    )
    best = best_applicable_coupon(offer, [worse, better], Decimal("500.00"), "BRL")
    assert best is not None
    assert best.final_amount == Decimal("420.00")
    assert best.coupon_id == better.id


def test_more_recent_but_worse_evidence_never_eliminates_older_better_one():
    """Cenário 7: a evidência mais recente é PIOR (desconto menor) -- não
    pode eliminar a evidência mais antiga, que é melhor."""
    offer = _offer()
    older_better = _coupon(
        code="SAME10", evidence="ev-old", discount_value=Decimal("80.00"), last_seen_at=NOW
    )
    newer_worse = _coupon(
        code="SAME10",
        evidence="ev-new",
        discount_value=Decimal("10.00"),
        last_seen_at=NOW.replace(hour=23),
    )
    best = best_applicable_coupon(
        offer, [older_better, newer_worse], Decimal("500.00"), "BRL"
    )
    assert best is not None
    assert best.final_amount == Decimal("420.00")
    assert best.coupon_id == older_better.id


def test_same_code_same_final_amount_ties_break_by_most_recent_last_seen_at():
    """Cenário 8: mesmo código, mesmo resultado econômico final --
    `last_seen_at` só entra como desempate DETERMINÍSTICO quando os
    preços finais empatam exatamente."""
    offer = _offer()
    older = _coupon(
        code="SAME10", evidence="ev-a", discount_value=Decimal("50.00"), last_seen_at=NOW
    )
    newer = _coupon(
        code="SAME10",
        evidence="ev-b",
        discount_value=Decimal("50.00"),
        last_seen_at=NOW.replace(hour=13),
    )
    best = best_applicable_coupon(offer, [older, newer], Decimal("500.00"), "BRL")
    assert best is not None
    assert best.coupon_id == newer.id


def test_empty_code_coupons_are_never_grouped_best_final_amount_still_wins():
    """Cenário 9: `code=""` nunca é agrupado por nenhum identificador
    (nem por engano via recência) -- entre três evidências sem código,
    vence a de maior desconto, nunca a mais recente nem a mais antiga
    por padrão."""
    offer = _offer()
    mediocre = _coupon(
        code="", evidence="ev-a", discount_value=Decimal("20.00"), last_seen_at=NOW
    )
    newest_worst = _coupon(
        code="",
        evidence="ev-b",
        discount_value=Decimal("5.00"),
        last_seen_at=NOW.replace(hour=23),
    )
    best_evidence = _coupon(
        code="",
        evidence="ev-c",
        discount_value=Decimal("90.00"),
        last_seen_at=NOW.replace(hour=1),
    )
    best = best_applicable_coupon(
        offer, [mediocre, newest_worst, best_evidence], Decimal("500.00"), "BRL"
    )
    assert best is not None
    assert best.final_amount == Decimal("410.00")
    assert best.coupon_id == best_evidence.id


def test_different_codes_are_never_merged():
    offer = _offer()
    a = _coupon(code="AAA", discount_value=Decimal("10.00"))
    b = _coupon(code="BBB", discount_value=Decimal("20.00"))
    best = best_applicable_coupon(offer, [a, b], Decimal("500.00"), "BRL")
    assert best is not None
    assert best.coupon_id == b.id  # maior desconto (BBB) vence, sem fusão


# ---------------------------------------------------------------------------
# best_applicable_coupon
# ---------------------------------------------------------------------------


def test_best_applicable_coupon_picks_lowest_final_price_never_sums():
    offer = _offer()
    small_discount = _coupon(
        code="SMALL", discount_kind="fixed_amount", discount_value=Decimal("10.00")
    )
    big_discount = _coupon(
        code="BIG", discount_kind="fixed_amount", discount_value=Decimal("80.00")
    )
    best = best_applicable_coupon(
        offer, [small_discount, big_discount], Decimal("500.00"), "BRL"
    )
    assert isinstance(best, AppliedCoupon)
    assert best.code == "BIG"
    assert best.final_amount == Decimal("420.00")
    assert best.original_amount == Decimal("500.00")


def test_best_applicable_coupon_none_when_nothing_applies():
    offer = _offer()
    inapplicable = _coupon(scope_kind=None)
    assert best_applicable_coupon(offer, [inapplicable], Decimal("500.00"), "BRL") is None


def test_best_applicable_coupon_ignores_expired_and_inapplicable_but_uses_valid():
    offer = _offer()
    expired = _coupon(code="OLD", status="expired", discount_value=Decimal("100.00"))
    not_applicable = _coupon(code="OTHER", scope_kind="product", scope_reference=None)
    valid = _coupon(code="VALID", discount_value=Decimal("15.00"))
    best = best_applicable_coupon(
        offer, [expired, not_applicable, valid], Decimal("200.00"), "BRL"
    )
    assert best is not None
    assert best.code == "VALID"
    assert best.final_amount == Decimal("185.00")


def test_original_amount_never_overwritten_by_discount():
    """Preço original preservado: `AppliedCoupon.original_amount` é
    sempre o valor de referência recebido, nunca alterado pelo cálculo."""
    offer = _offer()
    coupon = _coupon(discount_value=Decimal("10.00"))
    best = best_applicable_coupon(offer, [coupon], Decimal("321.99"), "BRL")
    assert best is not None
    assert best.original_amount == Decimal("321.99")
