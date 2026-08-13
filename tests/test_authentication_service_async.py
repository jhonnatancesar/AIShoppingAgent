"""Equivalentes assíncronos do serviço de autenticação (extensão da
TASK-079, usados pelo webhook Telegram) -- mesmas regras de
`tests/test_authentication_service.py`, cobrindo `issue_action_link_async`/
`has_active_session_async`/`logout_async` e os helpers privados `_async`."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.audit.models import AuditEntry
from app.authentication.models import CredentialAction, CredentialActionToken
from app.authentication.service import (
    AuthenticationError,
    AuthenticationRateLimited,
    has_active_session_async,
    issue_action_link_async,
    logout_async,
    token_digest,
)
from app.users.models import User, UserRole

NOW = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)


def _result(*, one: object | None = None, optional: object | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = one
    result.scalar_one_or_none.return_value = optional
    return result


def _user() -> User:
    return User(
        id=uuid4(),
        display_name="Cliente",
        role=UserRole.USER,
        is_active=True,
        telegram_user_id=778899,
        username="cliente",
    )


def test_issue_action_link_async_stores_only_digest_and_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    session = MagicMock()
    session.get = AsyncMock(return_value=None)
    session.scalar = AsyncMock(return_value=0)
    session.execute = AsyncMock()
    monkeypatch.setattr(
        "app.authentication.service.secrets.token_urlsafe",
        lambda size: "raw-token-that-is-not-persisted",
    )

    issued = asyncio.run(
        issue_action_link_async(
            session,
            user=user,
            action=CredentialAction.SET_PASSWORD,
            public_base_url="https://auth.example.test/",
            now=NOW,
        )
    )

    added = [call.args[0] for call in session.add.call_args_list]
    token = next(value for value in added if isinstance(value, CredentialActionToken))
    audit = next(value for value in added if isinstance(value, AuditEntry))
    assert token.token_hash == token_digest("raw-token-that-is-not-persisted")
    assert token.user_id == user.id
    assert token.telegram_user_id == user.telegram_user_id
    assert token.expires_at == NOW + timedelta(minutes=10)
    assert issued.url == (
        "https://auth.example.test/auth#password:raw-token-that-is-not-persisted"
    )
    assert audit.entry_metadata == {"action": "set_password"}


def test_issue_action_link_async_rejects_missing_telegram_identity() -> None:
    user = _user()
    user.telegram_user_id = None
    session = MagicMock()
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock()

    with pytest.raises(AuthenticationError):
        asyncio.run(
            issue_action_link_async(
                session,
                user=user,
                action=CredentialAction.SET_PASSWORD,
                public_base_url="https://auth.example.test",
                now=NOW,
            )
        )


def test_issue_recovery_link_async_is_persistently_rate_limited() -> None:
    user = _user()
    session = MagicMock()
    session.get = AsyncMock(
        return_value=object()
    )  # credential existente -- válido para recuperação
    session.scalar = AsyncMock(return_value=3)

    with pytest.raises(AuthenticationRateLimited):
        asyncio.run(
            issue_action_link_async(
                session,
                user=user,
                action=CredentialAction.RECOVER_PASSWORD,
                public_base_url="https://auth.example.test",
                now=NOW,
            )
        )


def test_session_validation_async_does_not_renew_and_logout_only_revokes() -> None:
    user = _user()
    session = MagicMock()
    session.execute = AsyncMock(return_value=_result(optional=uuid4()))

    active = asyncio.run(
        has_active_session_async(
            session,
            user_id=user.id,
            telegram_user_id=778899,
            now=NOW,
        )
    )
    assert active is True
    assert session.add.call_count == 0

    asyncio.run(logout_async(session, user=user, now=NOW))
    assert session.execute.call_count == 2
    audit = session.add.call_args.args[0]
    assert isinstance(audit, AuditEntry)
    assert audit.action == "authentication.logged_out"
