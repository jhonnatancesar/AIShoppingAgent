"""Testes do modelo persistente de auditoria."""

from uuid import uuid4

from app.audit.models import AuditEntry
from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB


def test_audit_entry_matches_data_contract() -> None:
    """A tabela deve conter somente os dados necessários à trilha imutável."""
    table = AuditEntry.__table__

    assert list(table.columns) == [
        table.c.id,
        table.c.actor_type,
        table.c.actor_id,
        table.c.action,
        table.c.resource_type,
        table.c.resource_id,
        table.c.metadata,
        table.c.created_at,
    ]
    assert table.c.actor_type.type.length == 32
    assert table.c.actor_id.nullable is True
    assert table.c.action.type.length == 120
    assert table.c.resource_type.type.length == 64
    assert table.c.resource_id.nullable is False
    assert isinstance(table.c.metadata.type, JSONB)
    assert table.c.metadata.nullable is False
    assert table.c.created_at.type.timezone is True
    assert "updated_at" not in table.columns


def test_audit_entry_rejects_blank_required_vocabulary() -> None:
    """Ator, ação e tipo de recurso devem ser textos significativos."""
    check_names = {
        constraint.name
        for constraint in AuditEntry.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert check_names == {
        "ck_audit_entries_actor_type_not_blank",
        "ck_audit_entries_action_not_blank",
        "ck_audit_entries_resource_type_not_blank",
        "ck_audit_entries_metadata_object",
    }


def test_audit_actor_reference_restricts_user_deletion() -> None:
    """Um usuário auditado não deve ser removido em cascata."""
    foreign_key = next(
        constraint
        for constraint in AuditEntry.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )
    element = tuple(foreign_key.elements)[0]

    assert tuple(foreign_key.columns)[0].name == "actor_id"
    assert element.target_fullname == "users.id"
    assert element.ondelete == "RESTRICT"


def test_audit_indexes_support_resource_and_actor_history() -> None:
    """Índices devem permitir histórico determinístico por recurso e ator."""
    indexes: dict[str, Index] = {
        index.name: index for index in AuditEntry.__table__.indexes
    }

    assert set(indexes) == {
        "ix_audit_entries_resource_history",
        "ix_audit_entries_actor_history",
    }
    assert tuple(
        column.name for column in indexes["ix_audit_entries_resource_history"].columns
    ) == ("resource_type", "resource_id", "created_at", "id")
    actor_index = indexes["ix_audit_entries_actor_history"]
    assert tuple(column.name for column in actor_index.columns) == (
        "actor_id",
        "created_at",
    )
    assert str(actor_index.dialect_options["postgresql"]["where"]) == (
        "actor_id IS NOT NULL"
    )


def test_audit_model_is_registered_in_shared_metadata() -> None:
    """Alembic deve enxergar a auditoria pelo registro central."""
    assert AuditEntry in REGISTERED_MODELS
    assert Base.metadata.tables["audit_entries"] is AuditEntry.__table__


def test_audit_entry_uses_safe_metadata_default() -> None:
    """Cada entrada deve receber um objeto de metadata independente."""
    first = AuditEntry(
        actor_type="system",
        action="offer.observed",
        resource_type="offer",
        resource_id=uuid4(),
    )
    second = AuditEntry(
        actor_type="system",
        action="offer.observed",
        resource_type="offer",
        resource_id=uuid4(),
    )

    default_factory = AuditEntry.__table__.c.metadata.default.arg
    first.entry_metadata = default_factory(None)
    second.entry_metadata = default_factory(None)

    assert first.entry_metadata == {}
    assert second.entry_metadata == {}
    assert first.entry_metadata is not second.entry_metadata
