"""Testes da resolução get-or-create de identidade via Telegram."""

from unittest.mock import MagicMock

import pytest
from app.users.models import User, UserRole
from app.users.service import get_or_create_telegram_user
from sqlalchemy.exc import IntegrityError


def _session() -> MagicMock:
    session = MagicMock()
    # Garante que uma exceção dentro do `with session.begin_nested():` se
    # propague, como o SAVEPOINT real faz, em vez de ser engolida pelo mock.
    session.begin_nested.return_value.__exit__.return_value = False
    return session


def _existing_user(telegram_user_id: int) -> User:
    return User(
        display_name="Já cadastrado",
        role=UserRole.USER,
        telegram_user_id=telegram_user_id,
    )


def test_creates_a_new_user_on_first_contact() -> None:
    session = _session()
    session.scalar.side_effect = [None]

    user = get_or_create_telegram_user(
        session, telegram_user_id=123456789, display_name="Fulano"
    )

    assert user.telegram_user_id == 123456789
    assert user.display_name == "Fulano"
    assert user.role is UserRole.USER
    session.add.assert_called_once_with(user)
    session.flush.assert_called_once()


def test_returns_the_existing_user_without_inserting_again() -> None:
    session = _session()
    existing = _existing_user(123456789)
    session.scalar.side_effect = [existing]

    user = get_or_create_telegram_user(
        session, telegram_user_id=123456789, display_name="Fulano"
    )

    assert user is existing
    session.add.assert_not_called()
    session.flush.assert_not_called()


def test_rejects_blank_display_name_before_any_query() -> None:
    session = _session()

    with pytest.raises(ValueError, match="display_name"):
        get_or_create_telegram_user(
            session, telegram_user_id=123456789, display_name="   "
        )

    session.scalar.assert_not_called()


def test_concurrent_first_contact_resolves_to_the_winning_insert() -> None:
    """Duas mensagens simultâneas do mesmo usuário novo não devem falhar."""
    session = _session()
    existing = _existing_user(123456789)
    session.scalar.side_effect = [None, existing]
    session.flush.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))

    user = get_or_create_telegram_user(
        session, telegram_user_id=123456789, display_name="Fulano"
    )

    assert user is existing
    assert session.scalar.call_count == 2


def test_concurrent_failure_reraises_when_user_still_not_found() -> None:
    session = _session()
    session.scalar.side_effect = [None, None]
    session.flush.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))

    with pytest.raises(IntegrityError):
        get_or_create_telegram_user(
            session, telegram_user_id=123456789, display_name="Fulano"
        )
