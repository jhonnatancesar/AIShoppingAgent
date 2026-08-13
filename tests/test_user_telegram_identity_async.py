"""Equivalente assíncrono da resolução get-or-create de identidade via
Telegram (extensão da TASK-079, usado pelo webhook) -- mesmas regras de
`tests/test_user_telegram_identity.py`."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.users.models import User, UserRole
from app.users.service import get_or_create_telegram_user_async
from sqlalchemy.exc import IntegrityError


def _session() -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock()
    session.flush = AsyncMock()
    # Garante que uma exceção dentro do `async with session.begin_nested():`
    # se propague, como o SAVEPOINT real faz, em vez de ser engolida pelo mock.
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    session.begin_nested = MagicMock(return_value=nested)
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

    user = asyncio.run(
        get_or_create_telegram_user_async(
            session, telegram_user_id=123456789, display_name="Fulano"
        )
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

    user = asyncio.run(
        get_or_create_telegram_user_async(
            session, telegram_user_id=123456789, display_name="Fulano"
        )
    )

    assert user is existing
    session.add.assert_not_called()
    session.flush.assert_not_called()


def test_rejects_blank_display_name_before_any_query() -> None:
    session = _session()

    with pytest.raises(ValueError, match="display_name"):
        asyncio.run(
            get_or_create_telegram_user_async(
                session, telegram_user_id=123456789, display_name="   "
            )
        )

    session.scalar.assert_not_called()


def test_concurrent_first_contact_resolves_to_the_winning_insert() -> None:
    """Duas mensagens simultâneas do mesmo usuário novo não devem falhar."""
    session = _session()
    existing = _existing_user(123456789)
    session.scalar.side_effect = [None, existing]
    session.flush.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))

    user = asyncio.run(
        get_or_create_telegram_user_async(
            session, telegram_user_id=123456789, display_name="Fulano"
        )
    )

    assert user is existing
    assert session.scalar.call_count == 2


def test_concurrent_failure_reraises_when_user_still_not_found() -> None:
    session = _session()
    session.scalar.side_effect = [None, None]
    session.flush.side_effect = IntegrityError("INSERT", {}, Exception("duplicate"))

    with pytest.raises(IntegrityError):
        asyncio.run(
            get_or_create_telegram_user_async(
                session, telegram_user_id=123456789, display_name="Fulano"
            )
        )
