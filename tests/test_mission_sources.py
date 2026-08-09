"""Testes das fontes selecionadas para uma missão."""

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.missions.models import MissionSource
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index


def test_mission_source_uses_composite_identity() -> None:
    table = MissionSource.__table__
    assert [column.name for column in table.columns] == [
        "mission_id",
        "store_id",
        "next_eligible_at",
        "consecutive_blocks",
        "created_at",
    ]
    assert tuple(column.name for column in table.primary_key.columns) == (
        "mission_id",
        "store_id",
    )
    assert table.c.created_at.type.timezone is True
    assert table.c.next_eligible_at.type.timezone is True
    assert table.c.next_eligible_at.nullable is True
    assert table.c.consecutive_blocks.nullable is False
    assert table.c.consecutive_blocks.default.arg == 0


def test_mission_source_backoff_columns_have_non_negative_constraint() -> None:
    constraint = next(
        constraint
        for constraint in MissionSource.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    )
    assert constraint.name == "ck_mission_sources_consecutive_blocks_non_negative"


def test_mission_source_references_history_with_restrict() -> None:
    foreign_keys = {
        tuple(constraint.columns)[0].name: tuple(constraint.elements)[0]
        for constraint in MissionSource.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys["mission_id"].target_fullname == "missions.id"
    assert foreign_keys["store_id"].target_fullname == "stores.id"
    assert all(element.ondelete == "RESTRICT" for element in foreign_keys.values())


def test_mission_source_has_reverse_lookup_and_registration() -> None:
    index = next(
        index for index in MissionSource.__table__.indexes if isinstance(index, Index)
    )
    assert index.name == "ix_mission_sources_store_id"
    assert tuple(column.name for column in index.columns) == ("store_id",)
    assert MissionSource in REGISTERED_MODELS
    assert Base.metadata.tables["mission_sources"] is MissionSource.__table__
