"""Contrato persistente da autenticação por senha."""

from app.authentication.models import (
    CredentialAction,
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)
from app.database.base import Base
from app.database.model_registry import REGISTERED_MODELS
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint


def test_authentication_models_share_registered_metadata() -> None:
    for model in (UserCredential, UserAuthSession, CredentialActionToken):
        assert model in REGISTERED_MODELS
        assert Base.metadata.tables[model.__tablename__] is model.__table__


def test_credential_action_is_closed() -> None:
    assert [action.value for action in CredentialAction] == [
        "set_password",
        "change_password",
        "login",
        "recover_password",
    ]


def test_credentials_have_restrict_owner_and_timezone_timestamps() -> None:
    table = UserCredential.__table__
    foreign_key = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    )
    assert foreign_key.ondelete == "RESTRICT"
    assert table.c.user_id.primary_key
    assert table.c.password_hash.type.length == 512
    assert table.c.password_changed_at.type.timezone
    assert table.c.recorded_at.server_default is not None


def test_sessions_enforce_absolute_ttl_and_permanent_revocation() -> None:
    table = UserAuthSession.__table__
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "12 hours" in checks["ck_user_auth_sessions_absolute_ttl"]
    assert "revoked_at" in checks["ck_user_auth_sessions_revocation_time"]
    assert table.c.authenticated_at.type.timezone
    assert table.c.expires_at.type.timezone
    assert any(
        index.name == "ix_user_auth_sessions_active_lookup" for index in table.indexes
    )


def test_action_tokens_are_hashed_unique_and_terminal_once() -> None:
    table = CredentialActionToken.__table__
    assert table.c.token_hash.type.length == 64
    assert table.c.expires_at.type.timezone
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"token_hash"}
        for constraint in table.constraints
    )
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "10 minutes" in checks["ck_credential_action_tokens_ttl"]
    assert "recover_password" in checks["credential_action_values"]
    assert "failed_attempts" in checks["ck_credential_action_tokens_failures"]
    assert "consumed_at" in checks["ck_credential_action_tokens_single_terminal"]
    assert all(isinstance(index, Index) for index in table.indexes)
