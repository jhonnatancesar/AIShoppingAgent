"""Cobertura de lógica pura de `app.collection.providers.stores` ainda sem
teste isolado -- normalização de parcelamento, classificação de vendedor/
parceiro e parsing do JSON SSR (`__NEXT_DATA__`) da Magalu. Nenhuma destas
funções toca rede, sessão ou `AsyncSession`; comportamento assíncrono/CDP
continua coberto pelos testes de `test_store_providers.py`."""

from app.collection.providers.stores import (
    MarketplacePartyKind,
    _amazon_card_seller_kind,
    _magalu_money,
    _magalu_rows_from_next_data,
    _mercado_livre_party_kind,
    _parse_installment_summary,
    _parse_pichau_installment_row,
    _parse_terabyte_installment_row,
)


def test_parse_installment_summary_returns_empty_triple_without_pattern_match() -> None:
    assert _parse_installment_summary("Frete grátis para todo o Brasil") == (
        None,
        None,
        False,
    )


def test_parse_pichau_installment_row_rejects_non_positive_count() -> None:
    assert _parse_pichau_installment_row("0x de R$10,00 (sem juros)") is None


def test_parse_terabyte_installment_row_rejects_non_positive_count() -> None:
    assert _parse_terabyte_installment_row("0x de R$ 10,00 s/juros*") is None


def test_amazon_card_seller_kind_marketplace_partner_for_third_party_seller() -> None:
    assert (
        _amazon_card_seller_kind("Vendido por Loja Parceira LTDA")
        == MarketplacePartyKind.MARKETPLACE_PARTNER.value
    )


def test_mercado_livre_party_kind_unknown_when_value_missing_or_blank() -> None:
    assert _mercado_livre_party_kind(None) is MarketplacePartyKind.UNKNOWN
    assert _mercado_livre_party_kind("   ") is MarketplacePartyKind.UNKNOWN


def test_magalu_money_none_for_none_and_boolean_values() -> None:
    assert _magalu_money(None) is None
    assert _magalu_money(True) is None


def test_magalu_money_none_for_value_that_is_not_a_valid_decimal() -> None:
    assert _magalu_money("não é dinheiro") is None


def test_magalu_money_none_for_negative_amount() -> None:
    assert _magalu_money(-10) is None


def test_magalu_rows_from_next_data_empty_when_next_data_script_absent() -> None:
    html = "<html><body><h1>Sem dados SSR aqui</h1></body></html>"
    assert _magalu_rows_from_next_data(html) == []


def test_magalu_rows_from_next_data_empty_when_json_missing_expected_keys() -> None:
    html = '<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>'
    assert _magalu_rows_from_next_data(html) == []


def test_magalu_rows_from_next_data_empty_when_items_is_not_a_list() -> None:
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props": {"pageProps": {"data": {"search": {"items": "não é lista"}}}}}'
        "</script>"
    )
    assert _magalu_rows_from_next_data(html) == []


def test_magalu_rows_from_next_data_skips_non_dict_items_and_offers() -> None:
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props": {"pageProps": {"data": {"search": {"items": '
        '["não é produto", {"id": "p1", "offers": "não é lista de ofertas"}]'
        "}}}}}</script>"
    )
    assert _magalu_rows_from_next_data(html) == []
