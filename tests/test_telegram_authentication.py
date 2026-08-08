from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from app.telegram.authentication import (
    TelegramAuthenticationFailure,
    TelegramAuthenticationResult,
    authenticate_telegram_user,
    webhook_secret_matches,
)
from app.telegram.contracts import TelegramChatType, TelegramMessage
from app.users.models import User
from pydantic import SecretStr


def _message(
    *,
    chat_type: TelegramChatType = TelegramChatType.PRIVATE,
    chat_id: int = 42,
    user_id: int = 42,
) -> TelegramMessage:
    return TelegramMessage(
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=user_id,
        text="/preferencias",
        received_at=datetime.now(UTC),
    )


def _user(*, active: bool = True) -> User:
    return cast(User, SimpleNamespace(is_active=active))


@pytest.mark.parametrize(
    ("provided", "expected", "matches"),
    [
        ("valid-secret", SecretStr("valid-secret"), True),
        ("wrong-secret", SecretStr("valid-secret"), False),
        (None, SecretStr("valid-secret"), False),
        ("valid-secret", None, False),
    ],
)
def test_webhook_secret_matches_only_configured_equal_values(
    provided: str | None,
    expected: SecretStr | None,
    matches: bool,
) -> None:
    assert webhook_secret_matches(provided, expected) is matches


def test_webhook_secret_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def _compare_digest(provided: str, expected: str) -> bool:
        calls.append((provided, expected))
        return True

    monkeypatch.setattr(
        "app.telegram.authentication.secrets.compare_digest", _compare_digest
    )

    assert webhook_secret_matches("provided", SecretStr("expected")) is True
    assert calls == [("provided", "expected")]


@pytest.mark.parametrize("chat_type", list(TelegramChatType)[1:])
def test_non_private_chat_is_rejected_before_user_resolution(
    monkeypatch: pytest.MonkeyPatch,
    chat_type: TelegramChatType,
) -> None:
    resolver = MagicMock(side_effect=AssertionError("must not resolve user"))
    monkeypatch.setattr(
        "app.telegram.authentication.get_or_create_telegram_user", resolver
    )

    result = authenticate_telegram_user(
        MagicMock(),
        message=_message(chat_type=chat_type),
        display_name="Pessoa",
    )

    assert result.failure is TelegramAuthenticationFailure.NON_PRIVATE_CHAT
    assert result.user is None
    resolver.assert_not_called()


def test_private_identity_mismatch_is_rejected_before_user_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = MagicMock(side_effect=AssertionError("must not resolve user"))
    monkeypatch.setattr(
        "app.telegram.authentication.get_or_create_telegram_user", resolver
    )

    result = authenticate_telegram_user(
        MagicMock(),
        message=_message(chat_id=41, user_id=42),
        display_name="Pessoa",
    )

    assert result.failure is TelegramAuthenticationFailure.IDENTITY_MISMATCH
    assert result.user is None
    resolver.assert_not_called()


@pytest.mark.parametrize(
    ("active", "expected_failure"),
    [
        (True, None),
        (False, TelegramAuthenticationFailure.INACTIVE_USER),
    ],
)
def test_private_matching_identity_resolves_and_checks_active_user(
    monkeypatch: pytest.MonkeyPatch,
    active: bool,
    expected_failure: TelegramAuthenticationFailure | None,
) -> None:
    user = _user(active=active)
    resolver = MagicMock(return_value=user)
    monkeypatch.setattr(
        "app.telegram.authentication.get_or_create_telegram_user", resolver
    )
    session = MagicMock()

    result = authenticate_telegram_user(
        session,
        message=_message(),
        display_name="Pessoa",
    )

    resolver.assert_called_once_with(
        session, telegram_user_id=42, display_name="Pessoa"
    )
    assert result.failure is expected_failure
    assert result.user is (user if active else None)


@pytest.mark.parametrize(
    ("user", "failure"),
    [(None, None), (_user(), TelegramAuthenticationFailure.INACTIVE_USER)],
)
def test_authentication_result_rejects_ambiguous_states(
    user: User | None,
    failure: TelegramAuthenticationFailure | None,
) -> None:
    with pytest.raises(ValueError, match="user or failure"):
        TelegramAuthenticationResult(user=user, failure=failure)
