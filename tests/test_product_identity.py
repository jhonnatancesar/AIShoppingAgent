"""Identidade determinística de produto/variante da TASK-097."""

from app.products.identity import (
    ProductRequestKind,
    classify_product_request,
    resolve_product_variant,
)


def test_specific_iphone_capacity_has_one_exact_identity() -> None:
    request = classify_product_request("iPhone 17 Pro 256GB")
    same = resolve_product_variant("Apple iPhone 17 Pro, 256 GB, preto")
    other_capacity = resolve_product_variant("Apple iPhone 17 Pro 512 GB")
    pro_max = resolve_product_variant("Apple iPhone 17 Pro Max 256 GB")

    assert request.kind is ProductRequestKind.SPECIFIC_PRODUCT
    assert same is not None and request.identity_key == same.identity_key
    assert other_capacity is not None and other_capacity.identity_key != request.identity_key
    assert pro_max is not None and pro_max.identity_key != request.identity_key


def test_iphone_family_has_no_specific_identity_and_keeps_optional_variant_scope() -> None:
    broad = classify_product_request("iPhone 17")
    pro = classify_product_request("iPhone 17 Pro")

    assert broad.kind is ProductRequestKind.PRODUCT_FAMILY
    assert broad.identity_key is None
    assert broad.variant is None
    assert pro.kind is ProductRequestKind.PRODUCT_FAMILY
    assert pro.family_key == broad.family_key
    assert pro.variant == "pro"


def test_generic_category_never_invents_product_identity() -> None:
    request = classify_product_request("cadeira gamer")

    assert request.kind is ProductRequestKind.GENERIC_CATEGORY
    assert request.family_key is None
    assert request.identity_key is None
    assert resolve_product_variant("cadeira gamer reclinável azul") is None


def test_missing_or_ambiguous_storage_never_resolves_variant() -> None:
    assert resolve_product_variant("iPhone 17 Pro") is None
    assert resolve_product_variant("iPhone 17 Pro 128 GB ou 256 GB") is None


def test_same_s24_ultra_capacity_converges_across_store_titles() -> None:
    amazon = resolve_product_variant(
        "Smartphone Samsung Galaxy S24 Ultra 512GB, 12GB RAM"
    )
    kabum = resolve_product_variant("Samsung Galaxy S24 Ultra 512 GB Titânio")
    smaller = resolve_product_variant("Samsung Galaxy S24 Ultra 256GB")

    assert amazon is not None and kabum is not None and smaller is not None
    assert amazon.identity_key == kabum.identity_key
    assert amazon.identity_key != smaller.identity_key

