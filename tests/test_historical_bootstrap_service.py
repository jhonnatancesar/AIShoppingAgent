"""Testes unitários das funções puras de app.historical_bootstrap.service.

Cobre `_source`, `_parse_candidate` e `_match` -- as únicas três funções do
módulo que não tocam `AsyncSession`/IA (segmentação AST: pure=30 linhas
faltantes). O restante do arquivo (funções `async def` que tocam
`AsyncSession`/`AIProviderManager`/`WebSearchManager`) é coberto pela suíte
de integração real e, quando aplicável, por testes unitários com sessão
mockada em rodada futura -- não duplicado aqui.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from app.historical_bootstrap.service import (
    _Match,
    _match,
    _parse_candidate,
    _source,
)
from app.products.models import Product

# ---------------------------------------------------------------------------
# _source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.pichau.com.br/produto/1", "pichau.com.br"),
        ("https://terabyteshop.com.br/x", "terabyteshop.com.br"),
        ("https://www.hardwarebarato.com/produto/2", "hardware_barato"),
        ("https://hardwarebarato.com/produto/2", "hardware_barato"),
    ],
)
def test_source_strips_www_and_special_cases_hardware_barato(url, expected):
    assert _source(url) == expected


def test_source_truncates_long_host_to_120_chars():
    long_host = "a" * 200
    url = f"https://{long_host}.com.br/produto"
    result = _source(url)
    assert len(result) == 120
    assert result == f"{long_host}.com.br"[:120]


# ---------------------------------------------------------------------------
# _parse_candidate
# ---------------------------------------------------------------------------


def test_parse_candidate_returns_none_when_no_price_found():
    assert (
        _parse_candidate(
            url="https://pichau.com.br/x", title="Placa-mãe", content="sem preço aqui"
        )
        is None
    )


def test_parse_candidate_parses_price_with_thousands_separator():
    candidate = _parse_candidate(
        url="https://pichau.com.br/x",
        title="Placa-mãe ASUS TUF B650",
        content="R$ 1.234,56 à vista",
    )
    assert candidate is not None
    assert candidate.amount == Decimal("1234.56")


def test_parse_candidate_parses_simple_price_without_thousands_separator():
    candidate = _parse_candidate(
        url="https://pichau.com.br/x", title="Placa-mãe", content="R$ 99,90 no boleto"
    )
    assert candidate is not None
    assert candidate.amount == Decimal("99.90")


def test_parse_candidate_uses_first_price_when_multiple_present():
    candidate = _parse_candidate(
        url="https://pichau.com.br/x",
        title="Placa-mãe",
        content="R$ 1.234,56 à vista ou R$ 1.500,00 parcelado",
    )
    assert candidate is not None
    assert candidate.amount == Decimal("1234.56")


def test_parse_candidate_extracts_valid_date():
    candidate = _parse_candidate(
        url="https://pichau.com.br/x",
        title="Placa-mãe",
        content="R$ 1.234,56 -- coletado em 15/03/2026",
    )
    assert candidate is not None
    assert candidate.historical_date == date(2026, 3, 15)


def test_parse_candidate_falls_back_to_none_date_when_missing():
    candidate = _parse_candidate(
        url="https://pichau.com.br/x",
        title="Placa-mãe",
        content="R$ 1.234,56 sem data nenhuma",
    )
    assert candidate is not None
    assert candidate.historical_date is None


def test_parse_candidate_falls_back_to_none_date_when_invalid_calendar_date():
    candidate = _parse_candidate(
        url="https://pichau.com.br/x",
        title="Placa-mãe",
        content="R$ 1.234,56 -- coletado em 31/02/2026",
    )
    assert candidate is not None
    assert candidate.historical_date is None


def test_parse_candidate_builds_source_and_safe_url_and_identity_text():
    candidate = _parse_candidate(
        url="https://www.pichau.com.br/produto/1?session_id=abc123",
        title="ASUS TUF Gaming B650-Plus",
        content="R$ 1.234,56 " + ("y" * 2000),
    )
    assert candidate is not None
    assert candidate.source == "pichau.com.br"
    assert "session_id" not in candidate.safe_url
    assert (
        candidate.identity_text
        == f"ASUS TUF Gaming B650-Plus {('R$ 1.234,56 ' + 'y' * 2000)[:1000]}"
    )
    assert candidate.match_kind == "exact"
    assert candidate.evidence_attributes == ()
    assert candidate.store_name is None


# ---------------------------------------------------------------------------
# _match
# ---------------------------------------------------------------------------


def _resolved(
    *,
    family_key: str = "asus-tuf-b650",
    identity_key: str = "v1:asus-tuf-b650",
    variant: str = "wifi",
    attributes: tuple[tuple[str, str], ...] = (),
) -> object:
    return type(
        "Resolved",
        (),
        {
            "family_key": family_key,
            "identity_key": identity_key,
            "variant": variant,
            "attributes": attributes,
        },
    )()


def _product(
    *,
    family_key: str | None = "asus-tuf-b650",
    variant: str | None = None,
    attributes: dict[str, str] | None = None,
) -> Product:
    kwargs: dict[str, object] = {
        "id": uuid4(),
        "name": "ASUS TUF Gaming B650-Plus WiFi",
        "display_name": "ASUS TUF Gaming B650-Plus WiFi",
        "family_key": family_key,
        "variant": variant,
    }
    if attributes is not None:
        kwargs["attributes"] = attributes
    return Product(**kwargs)


def test_match_returns_none_when_text_does_not_resolve(monkeypatch):
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant", lambda _text: None
    )
    assert _match(_product(), "texto qualquer") is None


def test_match_returns_none_when_product_has_no_family_key(monkeypatch):
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant",
        lambda _text: _resolved(),
    )
    assert _match(_product(family_key=None), "texto qualquer") is None


def test_match_returns_none_when_family_key_diverges(monkeypatch):
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant",
        lambda _text: _resolved(family_key="intel-i9"),
    )
    assert _match(_product(family_key="asus-tuf-b650"), "texto qualquer") is None


def test_match_returns_none_when_required_attribute_diverges(monkeypatch):
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant",
        lambda _text: _resolved(attributes=(("board_brand", "Gigabyte"),)),
    )
    product = _product(attributes={"board_brand": "Asus"})
    assert _match(product, "texto qualquer") is None


def test_match_returns_none_when_required_attribute_missing_from_evidence(monkeypatch):
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant",
        lambda _text: _resolved(attributes=()),
    )
    product = _product(attributes={"board_brand": "Asus"})
    assert _match(product, "texto qualquer") is None


def test_match_returns_none_when_variant_diverges(monkeypatch):
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant",
        lambda _text: _resolved(variant="no_wifi"),
    )
    product = _product(variant="wifi")
    assert _match(product, "texto qualquer") is None


def test_match_exact_when_identity_key_matches(monkeypatch):
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant",
        lambda _text: _resolved(identity_key="v1:asus-tuf-b650"),
    )
    product = Product(
        id=uuid4(),
        name="ASUS TUF Gaming B650-Plus WiFi",
        family_key="asus-tuf-b650",
        identity_key="v1:asus-tuf-b650",
    )
    result = _match(product, "texto qualquer")
    assert isinstance(result, _Match)
    assert result.kind == "exact"


def test_match_family_when_identity_key_diverges_but_family_and_attributes_match(
    monkeypatch,
):
    resolved = _resolved(
        identity_key="v1:asus-tuf-b650-gigabyte",
        attributes=(("board_brand", "Gigabyte"),),
    )
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant",
        lambda _text: resolved,
    )
    product = Product(
        id=uuid4(),
        name="ASUS TUF Gaming B650-Plus WiFi",
        family_key="asus-tuf-b650",
        identity_key="v1:asus-tuf-b650",
    )
    result = _match(product, "texto qualquer")
    assert result is not None
    assert result.kind == "family"
    assert result.resolved is resolved


def test_match_ignores_product_attributes_not_present_in_required_set(monkeypatch):
    """Missão genérica (sem `board_brand` exigido) aceita evidência de
    qualquer fabricante -- só compara as chaves que o Product de fato pediu."""
    resolved = _resolved(
        attributes=(("board_brand", "Gigabyte"), ("memory_type", "DDR5"))
    )
    monkeypatch.setattr(
        "app.historical_bootstrap.service.resolve_product_variant",
        lambda _text: resolved,
    )
    product = _product(attributes={})
    result = _match(product, "texto qualquer")
    assert result is not None
