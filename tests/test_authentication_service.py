"""Regras transacionais da TASK-061 em isolamento."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.audit.models import AuditEntry
from app.authentication.models import (
    CredentialAction,
    CredentialActionToken,
    UserAuthSession,
    UserCredential,
)
from app.authentication.passwords import PasswordPolicyError, hash_password
from app.authentication.service import (
    LOGIN_COOLDOWN,
    SESSION_TTL,
    TOKEN_FAILURE_LIMIT,
    TOKEN_TTL,
    AuthenticationError,
    AuthenticationRateLimited,
    _aware_now,
    _fail_token,
    _record_login_failure,
    _validated_base_url,
    complete_action,
    has_active_session,
    issue_action_link,
    logout,
    token_digest,
)
from app.events import Event
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


def _token(action: CredentialAction) -> CredentialActionToken:
    return CredentialActionToken(
        id=uuid4(),
        token_hash=token_digest("token-seguro"),
        action=action,
        user_id=uuid4(),
        telegram_user_id=778899,
        failed_attempts=0,
        created_at=NOW,
        expires_at=NOW + TOKEN_TTL,
    )


def test_issue_action_link_stores_only_digest_and_server_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    session = MagicMock()
    session.get.return_value = None
    session.execute.side_effect = [_result(one=0), MagicMock()]
    monkeypatch.setattr(
        "app.authentication.service.secrets.token_urlsafe",
        lambda size: "raw-token-that-is-not-persisted",
    )

    issued = issue_action_link(
        session,
        user=user,
        action=CredentialAction.SET_PASSWORD,
        public_base_url="https://auth.example.test/",
        now=NOW,
    )

    added = [call.args[0] for call in session.add.call_args_list]
    token = next(value for value in added if isinstance(value, CredentialActionToken))
    audit = next(value for value in added if isinstance(value, AuditEntry))
    assert token.token_hash == token_digest("raw-token-that-is-not-persisted")
    assert "raw-token" not in token.token_hash
    assert token.user_id == user.id
    assert token.telegram_user_id == user.telegram_user_id
    assert token.expires_at == NOW + timedelta(minutes=10)
    assert issued.url == (
        "https://auth.example.test/auth#password:raw-token-that-is-not-persisted"
    )
    assert audit.entry_metadata == {"action": "set_password"}


def test_issue_recovery_link_is_persistently_rate_limited() -> None:
    user = _user()
    session = MagicMock()
    session.get.return_value = UserCredential(
        user_id=user.id, password_hash="hash", password_changed_at=NOW
    )
    session.execute.return_value = _result(one=3)

    with pytest.raises(AuthenticationRateLimited):
        issue_action_link(
            session,
            user=user,
            action=CredentialAction.RECOVER_PASSWORD,
            public_base_url="https://auth.example.test",
            now=NOW,
        )


def test_issue_action_rejects_wrong_credential_state_and_missing_telegram() -> None:
    user = _user()
    session = MagicMock()
    session.get.return_value = None

    with pytest.raises(AuthenticationError):
        issue_action_link(
            session,
            user=user,
            action=CredentialAction.LOGIN,
            public_base_url="https://auth.example.test",
            now=NOW,
        )
    user.telegram_user_id = None
    with pytest.raises(AuthenticationError):
        issue_action_link(
            session,
            user=user,
            action=CredentialAction.SET_PASSWORD,
            public_base_url="https://auth.example.test",
            now=NOW,
        )


def test_login_consumes_token_and_creates_absolute_session() -> None:
    user = _user()
    token = _token(CredentialAction.LOGIN)
    token.user_id = user.id
    credential = UserCredential(
        user_id=user.id,
        password_hash=hash_password("frase secreta segura 03"),
        failed_login_attempts=2,
        login_window_started_at=NOW - timedelta(minutes=2),
        password_changed_at=NOW,
    )
    session = MagicMock()
    session.get.return_value = user
    session.execute.side_effect = [
        _result(optional=token),
        _result(optional=credential),
        MagicMock(),
    ]

    action = complete_action(
        session,
        raw_token="token-seguro",
        password="frase secreta segura 03",
        password_confirmation=None,
        now=NOW,
    )

    added = [call.args[0] for call in session.add.call_args_list]
    auth_session = next(value for value in added if isinstance(value, UserAuthSession))
    event = next(value for value in added if isinstance(value, Event))
    assert action is CredentialAction.LOGIN
    assert token.consumed_at == NOW
    assert auth_session.user_id == user.id
    assert auth_session.telegram_user_id == 778899
    assert auth_session.expires_at == NOW + SESSION_TTL
    assert event.event_type == "authentication.completed.v1"
    assert event.payload == {"user_id": str(user.id), "action": "login"}
    assert credential.failed_login_attempts == 0
    assert credential.login_window_started_at is None


def test_fifth_bad_login_locks_temporarily_and_does_not_consume_token() -> None:
    user = _user()
    token = _token(CredentialAction.LOGIN)
    token.user_id = user.id
    credential = UserCredential(
        user_id=user.id,
        password_hash=hash_password("frase secreta segura 04"),
        failed_login_attempts=4,
        login_window_started_at=NOW - timedelta(minutes=2),
        password_changed_at=NOW,
    )
    session = MagicMock()
    session.get.return_value = user
    session.execute.side_effect = [
        _result(optional=token),
        _result(optional=credential),
    ]

    with pytest.raises(AuthenticationError, match="Credenciais inválidas"):
        complete_action(
            session,
            raw_token="token-seguro",
            password="senha totalmente errada",
            password_confirmation=None,
            now=NOW,
        )

    assert credential.failed_login_attempts == 5
    assert credential.login_locked_until == NOW + LOGIN_COOLDOWN
    assert token.failed_attempts == 1
    assert token.consumed_at is None


@pytest.mark.parametrize(
    "action",
    [CredentialAction.CHANGE_PASSWORD, CredentialAction.RECOVER_PASSWORD],
)
def test_password_change_revokes_sessions_atomically(action: CredentialAction) -> None:
    user = _user()
    token = _token(action)
    token.user_id = user.id
    credential = UserCredential(
        user_id=user.id,
        password_hash=hash_password("frase secreta segura 05"),
        failed_login_attempts=0,
        password_changed_at=NOW - timedelta(days=1),
    )
    session = MagicMock()
    session.get.return_value = user
    session.execute.side_effect = [
        _result(optional=token),
        _result(optional=credential),
        MagicMock(),
    ]

    complete_action(
        session,
        raw_token="token-seguro",
        password="nova frase secreta segura 06",
        password_confirmation="nova frase secreta segura 06",
        now=NOW,
    )

    assert token.consumed_at == NOW
    assert credential.password_changed_at == NOW
    assert credential.failed_login_attempts == 0
    assert session.execute.call_count == 3


def test_set_password_requires_confirmation_and_blocks_common_password() -> None:
    user = _user()
    token = _token(CredentialAction.SET_PASSWORD)
    token.user_id = user.id
    session = MagicMock()
    session.get.return_value = user
    session.execute.side_effect = [
        _result(optional=token),
        _result(optional=None),
    ]
    with pytest.raises(PasswordPolicyError, match="conferem"):
        complete_action(
            session,
            raw_token="token-seguro",
            password="frase secreta segura 07",
            password_confirmation="frase diferente segura 08",
            now=NOW,
        )
    assert token.failed_attempts == 1

    token.failed_attempts = 0
    session.execute.side_effect = [
        _result(optional=token),
        _result(optional=None),
    ]
    with pytest.raises(PasswordPolicyError, match="comum"):
        complete_action(
            session,
            raw_token="token-seguro",
            password="passwordpassword",
            password_confirmation="passwordpassword",
            now=NOW,
        )
    assert token.failed_attempts == 1


@pytest.mark.parametrize("terminal", ["consumed", "invalidated", "expired"])
def test_terminal_or_expired_token_cannot_be_replayed(terminal: str) -> None:
    token = _token(CredentialAction.LOGIN)
    if terminal == "consumed":
        token.consumed_at = NOW
    elif terminal == "invalidated":
        token.invalidated_at = NOW
    else:
        token.expires_at = NOW
    session = MagicMock()
    session.execute.return_value = _result(optional=token)

    with pytest.raises(AuthenticationError, match="inválido ou expirado"):
        complete_action(
            session,
            raw_token="token-seguro",
            password="frase secreta segura 09",
            password_confirmation=None,
            now=NOW,
        )


def test_token_failure_is_bounded_and_terminal() -> None:
    token = _token(CredentialAction.LOGIN)
    token.failed_attempts = TOKEN_FAILURE_LIMIT - 1

    _fail_token(token, NOW)
    _fail_token(token, NOW + timedelta(seconds=1))

    assert token.failed_attempts == TOKEN_FAILURE_LIMIT
    assert token.invalidated_at == NOW


def test_login_failure_window_restarts_without_permanent_lock() -> None:
    credential = UserCredential(
        user_id=uuid4(),
        password_hash="hash",
        failed_login_attempts=4,
        login_window_started_at=NOW - timedelta(minutes=16),
        password_changed_at=NOW,
    )

    _record_login_failure(credential, NOW)

    assert credential.failed_login_attempts == 1
    assert credential.login_window_started_at == NOW
    assert credential.login_locked_until is None


def test_session_validation_does_not_renew_and_logout_only_revokes() -> None:
    user = _user()
    session = MagicMock()
    session.execute.return_value = _result(optional=uuid4())

    assert has_active_session(
        session,
        user_id=user.id,
        telegram_user_id=778899,
        now=NOW,
    )
    assert session.add.call_count == 0

    logout(session, user=user, now=NOW)
    assert session.execute.call_count == 2
    audit = session.add.call_args.args[0]
    assert isinstance(audit, AuditEntry)
    assert audit.action == "authentication.logged_out"


def test_url_and_timestamp_guards_fail_closed() -> None:
    assert _validated_base_url("http://localhost:8000/") == "http://localhost:8000"
    assert _validated_base_url("https://auth.example.test") == (
        "https://auth.example.test"
    )
    with pytest.raises(ValueError, match="absolute"):
        _validated_base_url("auth.example.test")
    with pytest.raises(ValueError, match="HTTPS"):
        _validated_base_url("http://auth.example.test")
    for unsafe in (
        "https://user:secret@auth.example.test",
        "https://auth.example.test/path",
        "https://auth.example.test?token=secret",
        "https://auth.example.test#fragment",
    ):
        with pytest.raises(ValueError, match="clean origin"):
            _validated_base_url(unsafe)
    with pytest.raises(ValueError, match="timezone-aware"):
        _aware_now(datetime(2026, 8, 8, 15, 0))
