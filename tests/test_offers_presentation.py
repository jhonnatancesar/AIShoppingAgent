"""TASK-114: título individual da Offer -- raw_evidence preferido, fallback
Product. Subtask 4 (auditoria GG Oferta, revisão): imagem -- a canônica do
Product é a principal (mesmo produto/variante deve mostrar a mesma imagem
em todas as lojas), a própria Offer é a alternativa em runtime."""

from types import SimpleNamespace

from app.offers.presentation import (
    resolve_offer_display_title,
    resolve_offer_image_chain,
)


def _product(*, display_name=None, name="Product genérico", canonical_image_url=None):
    return SimpleNamespace(
        display_name=display_name, name=name, canonical_image_url=canonical_image_url
    )


def _offer(*, image_url=None):
    return SimpleNamespace(image_url=image_url)


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


def test_image_uses_offers_own_when_there_is_no_canonical() -> None:
    """Item 1 da checagem final: Product sem canonical + Offer com imagem
    -> usa a Offer, sem alternativa (nada mais para tentar)."""
    offer = _offer(image_url="https://example.invalid/own-photo.jpg")
    product = _product(canonical_image_url=None)
    assert resolve_offer_image_chain(offer, product) == (
        "https://example.invalid/own-photo.jpg",
        None,
    )


def test_image_prefers_canonical_over_offers_own_different_image() -> None:
    """Item 2: Product com canonical + Offer com imagem DIFERENTE -> a
    canônica é a principal (é o objetivo da feature: mesmo produto/
    variante mostra a mesma imagem entre lojas); a Offer vira alternativa
    de runtime, não é descartada."""
    offer = _offer(image_url="https://example.invalid/own-photo.jpg")
    product = _product(canonical_image_url="https://example.invalid/canonical.jpg")
    assert resolve_offer_image_chain(offer, product) == (
        "https://example.invalid/canonical.jpg",
        "https://example.invalid/own-photo.jpg",
    )


def test_image_canonical_without_offer_image_has_no_fallback() -> None:
    offer = _offer(image_url=None)
    product = _product(canonical_image_url="https://example.invalid/canonical.jpg")
    assert resolve_offer_image_chain(offer, product) == (
        "https://example.invalid/canonical.jpg",
        None,
    )


def test_image_does_not_repeat_the_same_url_as_fallback() -> None:
    """Item 5: canonical == Offer.image_url -> nunca duas tentativas da
    mesma URL; alternativa fica `None`."""
    same_url = "https://example.invalid/mesma-foto.jpg"
    offer = _offer(image_url=same_url)
    product = _product(canonical_image_url=same_url)
    assert resolve_offer_image_chain(offer, product) == (same_url, None)


def test_image_is_none_when_neither_offer_nor_product_have_one() -> None:
    """Item 4: nenhuma imagem em lugar nenhum -> os dois candidatos `None`,
    quem chama decide o fallback visual/texto."""
    offer = _offer(image_url=None)
    product = _product(canonical_image_url=None)
    assert resolve_offer_image_chain(offer, product) == (None, None)
