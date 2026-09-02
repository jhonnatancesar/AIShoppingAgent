"""Criação de `User` com senha própria (Subtask 9) -- reaproveitada pelo
cadastro Web self-service e pelo endpoint ADMIN. Concorrência real (a
prova que realmente importa para "só uma conta, nunca 500") fica em
`tests/integration/test_registration_and_recovery.py`; aqui só a lógica
determinística (validação, papel padrão, mapeamento de erro)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from app.authentication.passwords import PasswordPolicyError
from app.users.models import UserRole
from app.users.registration import RegistrationError
from app.users.service import create_user_with_password_async


def _session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.mark.anyio
async def test_create_user_defaults_role_user_and_display_name_to_username() -> None:
    user = await create_user_with_password_async(
        _session(), username="clientenovo", email="cliente@example.com", password="Senha#Forte123"
    )
    assert user.role is UserRole.USER
    assert user.username == "clientenovo"
    assert user.display_name == "clientenovo"
    assert user.email == "cliente@example.com"


@pytest.mark.anyio
async def test_create_user_normalizes_email_case_and_whitespace() -> None:
    """Validação de segurança (Subtask 9): `uq_users_email` é uma
    constraint simples, case-sensitive -- só é suficiente porque todo
    ponto de escrita normaliza antes de gravar. `Foo@Example.com  ` e
    `foo@example.com` precisam virar o mesmo valor armazenado."""
    user = await create_user_with_password_async(
        _session(),
        username="clientenovoemail",
        email="  Foo@Example.COM  ",
        password="Senha#Forte123",
    )
    assert user.email == "foo@example.com"


@pytest.mark.anyio
async def test_create_user_accepts_custom_display_name() -> None:
    user = await create_user_with_password_async(
        _session(),
        username="clientenovo2",
        email=None,
        password="Senha#Forte123",
        display_name="Cliente Bonito",
    )
    assert user.display_name == "Cliente Bonito"


@pytest.mark.anyio
async def test_create_user_rejects_username_with_spaces() -> None:
    with pytest.raises(RegistrationError):
        await create_user_with_password_async(
            _session(), username="com espaco", email=None, password="Senha#Forte123"
        )


@pytest.mark.anyio
async def test_create_user_rejects_weak_password() -> None:
    with pytest.raises(PasswordPolicyError):
        await create_user_with_password_async(
            _session(), username="clientenovo3", email=None, password="12345678"
        )


@pytest.mark.anyio
async def test_create_user_allows_non_user_role_only_when_explicitly_passed() -> None:
    """O default é sempre USER -- só quem chama explicitamente com outro
    role (o endpoint ADMIN, já autorizado) consegue outra coisa. O
    cadastro Web self-service nunca passa `role`, então nunca sai daqui
    como ADMIN/DEV."""
    user = await create_user_with_password_async(
        _session(),
        username="admin_novo",
        email=None,
        password="Senha#Forte123",
        role=UserRole.ADMIN,
    )
    assert user.role is UserRole.ADMIN
