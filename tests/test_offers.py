"""Testes dos modelos persistentes de lojas e ofertas."""

from uuid import uuid4

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.offers.models import Offer
from app.stores.models import Store
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint


def test_store_table_matches_supporting_data_contract() -> None:
    """A loja mínima deve normalizar a origem sem antecipar um provider."""
    table = Store.__table__

    assert list(table.columns) == [
        table.c.id,
        table.c.code,
        table.c.name,
        table.c.base_url,
        table.c.is_active,
        table.c.created_at,
        table.c.updated_at,
    ]
    assert table.c.code.type.length == 64
    assert table.c.code.nullable is False
    assert table.c.name.type.length == 160
    assert table.c.base_url.nullable is False
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
        table.c.external_id,
        table.c.url,
        table.c.created_at,
        table.c.updated_at,
    ]
    assert table.c.product_id.nullable is False
    assert table.c.store_id.nullable is False
    assert table.c.external_id.nullable is True
    assert table.c.external_id.type.length == 255
    assert table.c.url.nullable is False
    assert "price" not in table.columns
    assert "availability" not in table.columns


def test_offer_foreign_keys_restrict_historical_deletion() -> None:
    """Produto e loja referenciados não podem ser apagados em cascata."""
    foreign_keys = {
        tuple(constraint.columns)[0].name: (
            tuple(constraint.elements)[0].target_fullname,
            tuple(constraint.elements)[0].ondelete,
        )
        for constraint in Offer.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }

    assert foreign_keys == {
        "product_id": ("products.id", "RESTRICT"),
        "store_id": ("stores.id", "RESTRICT"),
    }


def test_offer_identity_indexes_match_data_contract() -> None:
    """Identidades externas e URLs devem ser únicas dentro da mesma loja."""
    indexes: dict[str, Index] = {index.name: index for index in Offer.__table__.indexes}

    assert set(indexes) == {
        "ix_offers_product_id",
        "ix_offers_store_id",
        "uq_offers_store_external_id",
        "uq_offers_store_url",
    }
    external_id_index = indexes["uq_offers_store_external_id"]
    assert external_id_index.unique is True
    assert tuple(column.name for column in external_id_index.columns) == (
        "store_id",
        "external_id",
    )
    assert str(external_id_index.dialect_options["postgresql"]["where"]) == (
        "external_id IS NOT NULL"
    )
    assert indexes["uq_offers_store_url"].unique is True


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


def test_offer_and_store_are_registered_in_shared_metadata() -> None:
    """Alembic deve enxergar as duas tabelas pelo registro central."""
    assert Store in REGISTERED_MODELS
    assert Offer in REGISTERED_MODELS
    assert Base.metadata.tables["stores"] is Store.__table__
    assert Base.metadata.tables["offers"] is Offer.__table__


def test_offer_accepts_missing_external_id() -> None:
    """A URL deve identificar uma oferta quando a loja não expõe ID."""
    offer = Offer(
        product_id=uuid4(),
        store_id=uuid4(),
        url="https://loja.example/produto",
    )

    assert offer.external_id is None
