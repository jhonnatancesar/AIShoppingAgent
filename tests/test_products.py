"""Testes do modelo persistente de produtos canônicos."""

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.products.models import Product
from sqlalchemy import CheckConstraint, UniqueConstraint


def test_product_table_matches_data_contract() -> None:
    """Colunas, nulabilidade e limites devem refletir o modelo documentado."""
    table = Product.__table__

    assert table.name == "products"
    assert list(table.columns) == [
        table.c.id,
        table.c.name,
        table.c.brand,
        table.c.model,
        table.c.created_at,
        table.c.updated_at,
    ]
    assert table.c.id.primary_key is True
    assert table.c.name.nullable is False
    assert table.c.name.type.length == 300
    assert table.c.brand.nullable is True
    assert table.c.brand.type.length == 160
    assert table.c.model.nullable is True
    assert table.c.model.type.length == 160
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True


def test_product_table_rejects_blank_text_by_constraint() -> None:
    """O banco deve proteger os textos informados contra valores em branco."""
    check_names = {
        constraint.name
        for constraint in Product.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_names == {
        "ck_products_name_not_blank",
        "ck_products_brand_not_blank",
        "ck_products_model_not_blank",
    }


def test_product_name_is_not_a_deduplication_key() -> None:
    """Produtos distintos podem compartilhar nome sem unicidade artificial."""
    unique_constraints = [
        constraint
        for constraint in Product.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]

    assert unique_constraints == []


def test_product_model_is_registered_in_shared_metadata() -> None:
    """O Alembic deve enxergar o produto pelo registro central."""
    assert Product in REGISTERED_MODELS
    assert Base.metadata.tables["products"] is Product.__table__


def test_product_accepts_optional_brand_and_model() -> None:
    """Marca e modelo devem permanecer opcionais no domínio."""
    product = Product(name="Notebook")

    assert product.name == "Notebook"
    assert product.brand is None
    assert product.model is None
