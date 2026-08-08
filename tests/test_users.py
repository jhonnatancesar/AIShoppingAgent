"""Testes do modelo persistente de usuários."""

from datetime import UTC

from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from app.database.time import utc_now
from app.users.models import User, UserRole
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Enum,
    String,
    UniqueConstraint,
)


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
        table.c.telegram_user_id,
        table.c.username,
        table.c.email,
        table.c.favorite_stores,
        table.c.preferred_categories,
        table.c.registration_step,
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
    assert table.c.telegram_user_id.nullable is True
    assert isinstance(table.c.telegram_user_id.type, BigInteger)
    assert table.c.username.nullable is True
    assert table.c.username.type.length == 32
    assert table.c.email.nullable is True
    assert table.c.email.type.length == 254
    assert table.c.favorite_stores.nullable is False
    assert isinstance(table.c.favorite_stores.type, ARRAY)
    assert isinstance(table.c.favorite_stores.type.item_type, String)
    assert table.c.preferred_categories.nullable is False
    assert isinstance(table.c.preferred_categories.type, ARRAY)
    assert table.c.registration_step.nullable is True
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True


def test_user_telegram_user_id_is_unique() -> None:
    """A coluna deve identificar exclusivamente a pessoa, nunca a conversa."""
    table = User.__table__

    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert any(
        {column.name for column in constraint.columns} == {"telegram_user_id"}
        for constraint in unique_constraints
    )


def test_user_table_rejects_blank_display_name_by_constraint() -> None:
    """O banco deve proteger o nome contra valores vazios ou só com espaços."""
    check_names = {
        constraint.name
        for constraint in User.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_users_display_name_not_blank" in check_names
    assert "user_role_values" in check_names
    assert "ck_users_username_not_blank" in check_names
    assert "ck_users_email_not_blank" in check_names


def test_user_username_is_unique() -> None:
    """Nome de usuário do cadastro inicial (TASK-060) deve ser exclusivo."""
    table = User.__table__

    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert any(
        {column.name for column in constraint.columns} == {"username"}
        for constraint in unique_constraints
    )


def test_user_registration_fields_are_optional_before_persistence() -> None:
    """Campos do cadastro inicial (TASK-060) não são exigidos na construção.

    `favorite_stores`/`preferred_categories` só ganham `[]` via
    `server_default`/`default` no INSERT (mesmo padrão de `is_active`);
    antes de um flush real, o atributo Python permanece `None`.
    """
    user = User(display_name="Usuário de teste", role=UserRole.USER)

    assert user.username is None
    assert user.email is None
    assert user.registration_step is None


def test_user_model_is_registered_in_shared_metadata() -> None:
    """O Alembic deve enxergar o modelo sem importar módulos ad hoc."""
    assert User in REGISTERED_MODELS
    assert Base.metadata.tables["users"] is User.__table__


def test_user_accepts_typed_role() -> None:
    """A entidade deve expor o papel como vocabulário tipado."""
    user = User(display_name="Usuário de teste", role=UserRole.USER)

    assert user.display_name == "Usuário de teste"
    assert user.role is UserRole.USER


def test_user_accepts_optional_telegram_user_id() -> None:
    """`telegram_user_id` identifica a pessoa, sem exigência de canal."""
    without_telegram = User(display_name="Usuário de teste", role=UserRole.USER)
    with_telegram = User(
        display_name="Usuário do Telegram",
        role=UserRole.USER,
        telegram_user_id=123456789,
    )

    assert without_telegram.telegram_user_id is None
    assert with_telegram.telegram_user_id == 123456789


def test_utc_now_returns_timezone_aware_utc() -> None:
    """Timestamps criados pela aplicação devem ser conscientes de UTC."""
    current_time = utc_now()

    assert current_time.tzinfo is UTC
