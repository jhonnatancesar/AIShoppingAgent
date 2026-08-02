"""Testes dos modelos persistentes de lojas e ofertas."""

from uuid import uuid4

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.offers.models import Offer
from app.stores.models import Seller, Store, StoreSourceType
from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)


def test_store_table_matches_supporting_data_contract() -> None:
    """A loja mínima deve normalizar a origem sem antecipar um provider."""
    table = Store.__table__

    assert list(table.columns) == [
        table.c.id,
        table.c.code,
        table.c.name,
        table.c.base_url,
        table.c.source_type,
        table.c.is_active,
        table.c.created_at,
        table.c.updated_at,
    ]
    assert table.c.code.type.length == 64
    assert table.c.code.nullable is False
    assert table.c.name.type.length == 160
    assert table.c.base_url.nullable is False
    assert isinstance(table.c.source_type.type, Enum)
    assert table.c.source_type.type.enums == ["retailer", "marketplace"]
    assert table.c.source_type.type.native_enum is False
    assert table.c.is_active.nullable is False
    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(constraint.columns) == (table.c.code,)
        for constraint in table.constraints
    )


def test_store_table_enforces_normalized_required_text() -> None:
    """Código, nome e URL base devem ter proteção no banco."""
    check_names = {
        constraint.name
        for constraint in Store.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_names == {
        "ck_stores_code_snake_case",
        "ck_stores_name_not_blank",
        "ck_stores_base_url_not_blank",
        "store_source_type_values",
    }
    code_constraint = next(
        constraint
        for constraint in Store.__table__.constraints
        if constraint.name == "ck_stores_code_snake_case"
    )
    assert str(code_constraint.sqltext) == ("code ~ '^[a-z][a-z0-9]*(_[a-z0-9]+)*$'")


def test_offer_table_matches_data_contract() -> None:
    """Oferta deve guardar identidade estável, mas nunca preço corrente."""
    table = Offer.__table__

    assert list(table.columns) == [
        table.c.id,
        table.c.product_id,
        table.c.store_id,
        table.c.seller_id,
        table.c.external_id,
        table.c.url,
        table.c.created_at,
        table.c.updated_at,
    ]
    assert table.c.product_id.nullable is False
    assert table.c.store_id.nullable is False
    assert table.c.seller_id.nullable is True
    assert table.c.external_id.nullable is True
    assert table.c.external_id.type.length == 255
    assert table.c.url.nullable is False
    assert "price" not in table.columns
    assert "availability" not in table.columns


def test_offer_foreign_keys_restrict_historical_deletion() -> None:
    """Produto, fonte e vendedor não podem ser apagados em cascata."""
    foreign_keys = {
        tuple(column.name for column in constraint.columns): (
            tuple(element.target_fullname for element in constraint.elements),
            tuple(element.ondelete for element in constraint.elements),
        )
        for constraint in Offer.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert foreign_keys == {
        ("product_id",): (("products.id",), ("RESTRICT",)),
        ("store_id",): (("stores.id",), ("RESTRICT",)),
        ("seller_id", "store_id"): (
            ("sellers.id", "sellers.store_id"),
            ("RESTRICT", "RESTRICT"),
        ),
    }


def test_offer_identity_indexes_match_data_contract() -> None:
    """Identidade deve distinguir varejo e vendedores de marketplace."""
    indexes: dict[str, Index] = {index.name: index for index in Offer.__table__.indexes}

    assert set(indexes) == {
        "ix_offers_product_id",
        "ix_offers_store_id",
        "ix_offers_seller_id",
        "uq_offers_retailer_external_id",
        "uq_offers_marketplace_external_id",
        "uq_offers_retailer_url",
        "uq_offers_marketplace_url",
    }
    external_id_index = indexes["uq_offers_retailer_external_id"]
    assert external_id_index.unique is True
    assert tuple(column.name for column in external_id_index.columns) == (
        "store_id",
        "external_id",
    )
    assert str(external_id_index.dialect_options["postgresql"]["where"]) == (
        "seller_id IS NULL AND external_id IS NOT NULL"
    )
    marketplace_index = indexes["uq_offers_marketplace_external_id"]
    assert tuple(column.name for column in marketplace_index.columns) == (
        "store_id",
        "seller_id",
        "external_id",
    )
    assert indexes["uq_offers_retailer_url"].unique is True
    assert indexes["uq_offers_marketplace_url"].unique is True


def test_offer_table_rejects_blank_identifiers_and_url() -> None:
    """Textos informados devem permanecer significativos."""
    check_names = {
        constraint.name
        for constraint in Offer.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_names == {
        "ck_offers_external_id_not_blank",
        "ck_offers_url_not_blank",
    }


def test_offer_store_and_seller_are_registered_in_shared_metadata() -> None:
    """Alembic deve enxergar fontes, vendedores e ofertas."""
    assert Store in REGISTERED_MODELS
    assert Seller in REGISTERED_MODELS
    assert Offer in REGISTERED_MODELS
    assert Base.metadata.tables["stores"] is Store.__table__
    assert Base.metadata.tables["sellers"] is Seller.__table__
    assert Base.metadata.tables["offers"] is Offer.__table__


def test_offer_accepts_missing_external_id() -> None:
    """A URL deve identificar uma oferta quando a loja não expõe ID."""
    offer = Offer(
        product_id=uuid4(),
        store_id=uuid4(),
        url="https://loja.example/produto",
    )

    assert offer.external_id is None
    assert offer.seller_id is None


def test_seller_matches_marketplace_identity_contract() -> None:
    table = Seller.__table__
    assert [column.name for column in table.columns] == [
        "id",
        "store_id",
        "external_id",
        "name",
        "created_at",
        "updated_at",
    ]
    assert table.c.store_id.nullable is False
    assert table.c.external_id.nullable is True
    assert table.c.name.type.length == 200
    assert {index.name for index in table.indexes} == {
        "ix_sellers_store_id",
        "uq_sellers_store_external_id",
    }


def test_store_defaults_to_retailer() -> None:
    assert Store.__table__.c.source_type.default.arg is StoreSourceType.RETAILER
