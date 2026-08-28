"""Identidade determinística de produto/variante da TASK-097."""

import pytest

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



# TASK-114 (DEC-105): _cpu()/_intel_cpu() nunca podem classificar como CPU
# um produto que só CITA compatibilidade com Ryzen/Intel -- achado real em
# PROD (placa-mãe X870E/B550 virando "Product CPU AMD Ryzen 9000/5000").


@pytest.mark.parametrize(
    "title",
    [
        "AMD Ryzen 9 9950X",
        "AMD Ryzen 7 9800X3D",
        "AMD Ryzen 7 5700, 3.7GHz (4.6GHz Turbo), 8-Cores 16-Threads, AM4, "
        "Com Cooler AMD Wraith Stealth, 100-100000743SBX",
    ],
)
def test_real_amd_cpu_resolves_as_cpu(title: str) -> None:
    identity = resolve_product_variant(title)
    assert identity is not None
    assert identity.category == "cpu"
    assert identity.brand == "amd"


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Intel Core i9-14900K", "core-i9"),
        ("Intel Core i7-14700K", "core-i7"),
        ("Intel Core i5-14600K", "core-i5"),
        ("Intel Core i3-14100", "core-i3"),
    ],
)
def test_real_intel_cpu_resolves_as_cpu(title: str, family: str) -> None:
    identity = resolve_product_variant(title)
    assert identity is not None
    assert identity.category == "cpu"
    assert identity.brand == "intel"
    assert identity.family == family


@pytest.mark.parametrize(
    "title",
    [
        # Achado real em PROD (missão "Placa-mãe MSI Tomahawk X870E").
        "MSI Placa-mãe MAG X870E Tomahawk MAX WiFi, ATX - Suporta processadores "
        "AMD Ryzen 9000/8000/7000, AM5-80A SPS VRM, DDR5 Memory Boost 8400+ MT/s "
        "(OC), PCIe 5.0 x16, M.2 Gen5, Wi-Fi 7, 5G LAN",
        # Achado real em PROD (missão "Placa Mãe AMD B550").
        "GIGABYTE Placa-mãe B550 AORUS Elite AX V3 AMD AM4 ATX, suporta "
        "processadores Ryzen 5000/4000/3000, DDR4, fase de alimentação 12+2",
        # Achado real durante o teste do reparo histórico -- sem verbo de
        # ligação, só a especificação solta.
        "Placa-Mãe Husky Nexus B550, M.2 NVMe, DDR4 3200MHz, Ryzen 5000/3000, "
        "Micro-ATX, HDMI/DP, 64GB Dual Channel - HPM550",
        "placa-mãe compatível com Intel Core i9-14900K",
        "cooler para Ryzen/Intel",
        "RAM otimizada para Ryzen",
        "Water Cooler compatível com AMD AM5/Intel LGA1700",
    ],
)
def test_cpu_compatibility_mention_never_resolves_as_cpu(title: str) -> None:
    identity = resolve_product_variant(title)
    assert identity is None or identity.category != "cpu"


def test_cpu_plus_motherboard_bundle_is_unresolved_not_wrong() -> None:
    """Fail-closed: um bundle real (CPU + placa-mãe juntos) é ambíguo demais
    para decidir determinística e conservadoramente -- melhor identidade não
    resolvida do que herdar a identidade errada de outro anúncio."""
    identity = resolve_product_variant(
        "Processador AMD Ryzen 7 7800X3D + Placa Mãe ASUS ROG STRIX X870E-H "
        "GAMING WIFI7"
    )
    assert identity is None
