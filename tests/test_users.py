"""Testes do modelo persistente de usuários."""

from datetime import UTC

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.users.models import User, UserRole, utc_now
from sqlalchemy import CheckConstraint, Enum


def test_user_role_has_only_v1_profiles() -> None:
    """O modelo não deve antecipar o perfil PLUS."""
    assert [role.value for role in UserRole] == ["USER", "ADMIN", "DEV"]


def test_user_table_matches_data_contract() -> None:
    """Colunas, nulabilidade e limites devem refletir o modelo documentado."""
    table = User.__table__

    assert table.name == "users"
    assert list(table.columns) == [
        table.c.id,
        table.c.display_name,
        table.c.role,
        table.c.is_active,
        table.c.created_at,
        table.c.updated_at,
    ]
    assert table.c.id.primary_key is True
    assert table.c.display_name.nullable is False
    assert table.c.display_name.type.length == 160
    assert table.c.role.nullable is False
    assert isinstance(table.c.role.type, Enum)
    assert table.c.role.type.enums == ["USER", "ADMIN", "DEV"]
    assert table.c.role.type.native_enum is False
    assert table.c.is_active.nullable is False
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True


def test_user_table_rejects_blank_display_name_by_constraint() -> None:
    """O banco deve proteger o nome contra valores vazios ou só com espaços."""
    check_names = {
        constraint.name
        for constraint in User.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_users_display_name_not_blank" in check_names
    assert "user_role_values" in check_names


def test_user_model_is_registered_in_shared_metadata() -> None:
    """O Alembic deve enxergar o modelo sem importar módulos ad hoc."""
    assert REGISTERED_MODELS == (User,)
    assert Base.metadata.tables["users"] is User.__table__


def test_user_accepts_typed_role() -> None:
    """A entidade deve expor o papel como vocabulário tipado."""
    user = User(display_name="Usuário de teste", role=UserRole.USER)

    assert user.display_name == "Usuário de teste"
    assert user.role is UserRole.USER


def test_utc_now_returns_timezone_aware_utc() -> None:
    """Timestamps criados pela aplicação devem ser conscientes de UTC."""
    current_time = utc_now()

    assert current_time.tzinfo is UTC
