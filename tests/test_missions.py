"""Testes do modelo persistente de missões."""

from uuid import uuid4

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.missions.models import Mission, MissionStatus
from sqlalchemy import CheckConstraint, Enum, ForeignKeyConstraint, Index


def test_mission_status_matches_lifecycle_contract() -> None:
    """Persistência não deve criar estados além dos definidos na TASK-018."""
    assert [status.value for status in MissionStatus] == [
        "draft",
        "active",
        "paused",
        "completed",
        "cancelled",
        "expired",
    ]


def test_mission_table_matches_data_contract() -> None:
    """Colunas, tipos e nulabilidade devem representar o estado atual."""
    table = Mission.__table__

    assert list(table.columns) == [
        table.c.id,
        table.c.user_id,
        table.c.title,
        table.c.status,
        table.c.expires_at,
        table.c.state_version,
        table.c.created_at,
        table.c.updated_at,
        table.c.prelist_sent,
        table.c.prelist_errata_sent,
        table.c.prelist_lowest_amount,
        table.c.prelist_lowest_currency,
    ]
    assert table.c.user_id.nullable is False
    assert table.c.title.type.length == 200
    assert isinstance(table.c.status.type, Enum)
    assert table.c.status.type.enums == [status.value for status in MissionStatus]
    assert table.c.status.type.native_enum is True
    assert table.c.expires_at.nullable is True
    assert table.c.state_version.nullable is False
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert table.c.prelist_sent.nullable is False
    assert table.c.prelist_errata_sent.nullable is False
    assert table.c.prelist_lowest_amount.nullable is True
    assert table.c.prelist_lowest_currency.nullable is True
    assert table.c.prelist_lowest_currency.type.length == 3


def test_mission_constraints_protect_core_invariants() -> None:
    """Banco deve rejeitar título vazio, prazo inválido e versão negativa."""
    check_names = {
        constraint.name
        for constraint in Mission.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_names == {
        "ck_missions_title_not_blank",
        "ck_missions_expiration_after_creation",
        "ck_missions_state_version_non_negative",
        "ck_missions_prelist_lowest_pair",
        "ck_missions_prelist_lowest_amount_non_negative",
        "ck_missions_prelist_lowest_currency_iso4217",
        "ck_missions_prelist_errata_requires_sent",
    }


def test_mission_owner_reference_restricts_user_deletion() -> None:
    """Missões não devem desaparecer em cascata com seu proprietário."""
    foreign_key = next(
        constraint
        for constraint in Mission.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )
    element = tuple(foreign_key.elements)[0]

    assert tuple(foreign_key.columns)[0].name == "user_id"
    assert element.target_fullname == "users.id"
    assert element.ondelete == "RESTRICT"


def test_mission_indexes_match_expected_queries() -> None:
    """Índices devem atender listagem do usuário e expiração pendente."""
    indexes: dict[str, Index] = {
        index.name: index for index in Mission.__table__.indexes
    }

    assert set(indexes) == {
        "ix_missions_user_status_created_at",
        "ix_missions_pending_expiration",
    }
    owner_index = indexes["ix_missions_user_status_created_at"]
    assert tuple(expression.name for expression in owner_index.expressions[:2]) == (
        "user_id",
        "status",
    )
    assert str(owner_index.expressions[2]) == "created_at DESC"
    expiration_index = indexes["ix_missions_pending_expiration"]
    assert tuple(column.name for column in expiration_index.columns) == (
        "status",
        "expires_at",
    )
    assert str(expiration_index.dialect_options["postgresql"]["where"]) == (
        "expires_at IS NOT NULL AND status NOT IN ('completed', 'cancelled', 'expired')"
    )


def test_mission_model_is_registered_in_shared_metadata() -> None:
    """Alembic deve enxergar missões pelo registro central."""
    assert Mission in REGISTERED_MODELS
    assert Base.metadata.tables["missions"] is Mission.__table__


def test_new_mission_defaults_to_draft_without_expiration() -> None:
    """Toda missão deve nascer como rascunho e pode ser permanente."""
    mission = Mission(user_id=uuid4(), title="Comprar notebook")

    assert mission.expires_at is None
    assert Mission.__table__.c.status.default.arg is MissionStatus.DRAFT
    assert Mission.__table__.c.state_version.default.arg == 0
