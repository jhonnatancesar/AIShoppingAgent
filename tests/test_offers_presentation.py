"""TASK-114: título individual da Offer -- raw_evidence preferido, fallback Product."""

from types import SimpleNamespace

from app.offers.presentation import resolve_offer_display_title


def _product(*, display_name=None, name="Product genérico"):
    return SimpleNamespace(display_name=display_name, name=name)


def _observation(*, title=None):
    return SimpleNamespace(raw_evidence={"title": title} if title else {})


def test_prefers_raw_evidence_title_over_product() -> None:
    product = _product(display_name="Amd Ryzen 9000")
    observation = _observation(title="MSI Placa-mãe MAG X870E Tomahawk WiFi")
    assert (
        resolve_offer_display_title(product, observation)
        == "MSI Placa-mãe MAG X870E Tomahawk WiFi"
    )


def test_different_offers_same_product_show_own_titles() -> None:
    product = _product(display_name="Amd Ryzen 9000")
    title_a = resolve_offer_display_title(product, _observation(title="Placa A"))
    title_b = resolve_offer_display_title(product, _observation(title="Placa B"))
    assert title_a == "Placa A"
    assert title_b == "Placa B"
    assert title_a != title_b


def test_falls_back_to_product_display_name_when_raw_title_missing() -> None:
    product = _product(display_name="MSI X870E Gaming Plus WiFi")
    assert resolve_offer_display_title(product, _observation()) == "MSI X870E Gaming Plus WiFi"


def test_falls_back_to_product_name_when_no_display_name() -> None:
    product = _product(display_name=None, name="Placa Mãe MSI X870E GAMING PLUS WIFI")
    assert (
        resolve_offer_display_title(product, None)
        == "Placa Mãe MSI X870E GAMING PLUS WIFI"
    )


def test_blank_raw_title_falls_back_to_product() -> None:
    product = _product(display_name="MSI X870E Gaming Plus WiFi")
    observation = SimpleNamespace(raw_evidence={"title": "   "})
    assert resolve_offer_display_title(product, observation) == "MSI X870E Gaming Plus WiFi"
