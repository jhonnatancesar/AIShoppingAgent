"""Testes das fontes selecionadas para uma missão."""

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.missions.models import MissionSource
from sqlalchemy import CheckConstraint, ForeignKeyConstraint


def test_mission_source_uses_composite_identity() -> None:
    """Achado (auditoria TASK-112 fase 3B, correção estrutural):
    `next_run_at`/`last_run_at` foram adicionados -- `MissionSource` passa
    a ser a unidade autoritativa de cadência do caminho legado, nunca mais
    `MissionSchedule` (missão inteira)."""
    table = MissionSource.__table__
    assert [column.name for column in table.columns] == [
        "mission_id",
        "store_id",
        "next_run_at",
        "last_run_at",
        "next_eligible_at",
        "consecutive_blocks",
        "created_at",
    ]
    assert tuple(column.name for column in table.primary_key.columns) == (
        "mission_id",
        "store_id",
    )
    assert table.c.created_at.type.timezone is True
    assert table.c.next_run_at.type.timezone is True
    assert table.c.next_run_at.nullable is True
    assert table.c.last_run_at.type.timezone is True
    assert table.c.last_run_at.nullable is True
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
    indexes = {index.name: index for index in MissionSource.__table__.indexes}
    reverse_lookup = indexes["ix_mission_sources_store_id"]
    assert tuple(column.name for column in reverse_lookup.columns) == ("store_id",)
    assert MissionSource in REGISTERED_MODELS
    assert Base.metadata.tables["mission_sources"] is MissionSource.__table__


def test_mission_source_has_no_dedicated_due_index() -> None:
    """TASK-112 fase 3B, auditoria com EXPLAIN ANALYZE (`tests/integration/
    test_mission_source_index_explain.py`): a query real de seleção
    (`claim_due_collections`/`claim_due_work`) ancora em `missions`
    (pequena, já filtrada por status) e alcança `mission_sources` pelos
    índices que já existiam -- zero sequential scan mesmo em volume bem
    acima da escala real. Um índice extra em `next_run_at` (coluna que
    muda a cada claim bem-sucedida) só custaria escrita sem benefício de
    leitura comprovado -- não projetado sem evidência."""
    assert "ix_mission_sources_due" not in {
        index.name for index in MissionSource.__table__.indexes
    }
