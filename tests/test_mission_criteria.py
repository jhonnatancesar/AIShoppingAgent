"""Testes do modelo persistente de critérios de missão."""

from decimal import Decimal
from uuid import uuid4

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.missions.models import MissionCriteria
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Numeric, UniqueConstraint


def test_mission_criteria_table_matches_data_contract() -> None:
    """Critérios devem representar busca e preço sem JSONB extensível.

    Achado (auditoria TASK-112 fase 3B): esta lista ficou desatualizada
    desde a TASK-097 (identidade de produto/seleção de variante), que
    nunca foi refletida aqui -- confirmado que a defasagem já existia
    antes da fase 3B (baseline `471e898`), não é regressão desta fase."""
    table = MissionCriteria.__table__

    assert list(table.columns) == [
        table.c.id,
        table.c.mission_id,
        table.c.search_query,
        table.c.model,
        table.c.target_amount,
        table.c.target_currency,
        table.c.request_kind,
        table.c.requested_family_key,
        table.c.requested_identity_key,
        table.c.requested_variant,
        table.c.variant_selection_mode,
        table.c.variant_prompted_at,
        table.c.created_at,
        table.c.updated_at,
    ]
    assert table.c.mission_id.nullable is False
    assert table.c.search_query.nullable is False
    assert table.c.model.nullable is True
    assert isinstance(table.c.target_amount.type, Numeric)
    assert table.c.target_amount.type.precision == 19
    assert table.c.target_amount.type.scale == 4
    assert table.c.target_currency.type.length == 3
    assert table.c.target_amount.nullable is True
    assert table.c.target_currency.nullable is True
    assert "recurrence" not in table.columns
    assert "filters" not in table.columns


def test_mission_criteria_is_unique_per_mission() -> None:
    """Cada missão deve possuir no máximo um conjunto editável de critérios."""
    table = MissionCriteria.__table__

    assert any(
        isinstance(constraint, UniqueConstraint)
        and tuple(constraint.columns) == (table.c.mission_id,)
        for constraint in table.constraints
    )


def test_mission_criteria_reference_restricts_mission_deletion() -> None:
    """Critérios não devem desaparecer por exclusão em cascata."""
    foreign_key = next(
        constraint
        for constraint in MissionCriteria.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )
    element = tuple(foreign_key.elements)[0]

    assert element.target_fullname == "missions.id"
    assert element.ondelete == "RESTRICT"


def test_mission_criteria_constraints_protect_search_and_target() -> None:
    """Busca, valor e moeda devem permanecer coerentes no banco.

    Achado (auditoria TASK-112 fase 3B): mesma defasagem da TASK-097
    (`request_kind`/`variant_selection_mode`) do teste acima, já existente
    antes desta fase."""
    check_names = {
        constraint.name
        for constraint in MissionCriteria.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_names == {
        "ck_mission_criteria_search_query_not_blank",
        "ck_mission_criteria_model_not_blank",
        "ck_mission_criteria_target_amount_non_negative",
        "ck_mission_criteria_target_pair",
        "ck_mission_criteria_currency_iso4217",
        "ck_mission_criteria_request_kind_values",
        "ck_mission_criteria_variant_selection_mode_values",
        "ck_mission_criteria_product_request_shape",
    }


def test_mission_criteria_model_is_registered_in_shared_metadata() -> None:
    """Alembic deve enxergar critérios pelo registro central."""
    assert MissionCriteria in REGISTERED_MODELS
    assert Base.metadata.tables["mission_criteria"] is MissionCriteria.__table__


def test_mission_criteria_accepts_optional_target_pair() -> None:
    """Uma busca pode existir sem preço-alvo ou usar valor monetário exato."""
    without_target = MissionCriteria(
        mission_id=uuid4(),
        search_query="notebook com 32 GB",
    )
    with_target = MissionCriteria(
        mission_id=uuid4(),
        search_query="notebook com 32 GB",
        target_amount=Decimal("5000.0000"),
        target_currency="BRL",
    )

    assert without_target.target_amount is None
    assert without_target.target_currency is None
    assert with_target.target_amount == Decimal("5000.0000")
    assert with_target.target_currency == "BRL"
