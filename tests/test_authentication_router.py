"""Contrato HTTP do formulário seguro da TASK-061."""

from unittest.mock import MagicMock

import pytest
from app.authentication.models import CredentialAction
from app.authentication.passwords import PasswordPolicyError
from app.authentication.router import (
    CompleteCredentialAction,
    authentication_form,
    complete_credential_action,
)
from app.authentication.service import AuthenticationError
from pydantic import SecretStr, ValidationError


def _payload(**overrides: object) -> CompleteCredentialAction:
    values: dict[str, object] = {
        "token": "token-canary",
        "password": "senha-canary-muito-segura",
        "password_confirmation": None,
    }
    values.update(overrides)
    return CompleteCredentialAction(**values)  # type: ignore[arg-type]


def test_form_keeps_token_in_fragment_and_sends_no_identity_fields() -> None:
    response = authentication_form()
    body = response.body.decode()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert "location.hash" in body
    assert "history.replaceState" in body
    assert "user_id" not in body
    assert "telegram_user_id" not in body
    assert '"role":' not in body


def test_payload_rejects_browser_supplied_identity_or_action() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CompleteCredentialAction(
            token="token-canary",
            password="senha-canary-muito-segura",
            user_id="00000000-0000-0000-0000-000000000001",  # type: ignore[call-arg]
            telegram_user_id=1,  # type: ignore[call-arg]
            action="login",  # type: ignore[call-arg]
            role="DEV",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "field",
    ["token", "password", "password_confirmation"],
)
def test_payload_bounds_all_authentication_material(field: str) -> None:
    values = {
        "token": "token",
        "password": "senha",
        "password_confirmation": "confirmação",
    }
    values[field] = "x" * 129

    with pytest.raises(ValidationError, match="at most 128"):
        CompleteCredentialAction(**values)


def test_http_action_passes_only_secrets_to_server_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_complete(session: object, **kwargs: object) -> CredentialAction:
        calls.append(kwargs)
        return CredentialAction.LOGIN

    monkeypatch.setattr("app.authentication.router.complete_action", fake_complete)

    response = complete_credential_action(_payload(), MagicMock())

    assert response.status_code == 200
    assert calls == [
        {
            "raw_token": "token-canary",
            "password": "senha-canary-muito-segura",
            "password_confirmation": None,
        }
    ]
    assert "Login concluído" in response.body.decode()


@pytest.mark.parametrize(
    ("action", "message"),
    [
        (CredentialAction.SET_PASSWORD, "Senha criada"),
        (CredentialAction.CHANGE_PASSWORD, "sessões anteriores revogadas"),
        (CredentialAction.RECOVER_PASSWORD, "sessões anteriores revogadas"),
    ],
)
def test_http_success_messages_follow_server_action(
    monkeypatch: pytest.MonkeyPatch,
    action: CredentialAction,
    message: str,
) -> None:
    monkeypatch.setattr(
        "app.authentication.router.complete_action",
        lambda *args, **kwargs: action,
    )

    response = complete_credential_action(
        _payload(password_confirmation=SecretStr("senha-canary-muito-segura")),
        MagicMock(),
    )

    assert response.status_code == 200
    assert message in response.body.decode()


def test_http_uses_generic_error_for_authentication_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AuthenticationError("detalhe interno")

    monkeypatch.setattr("app.authentication.router.complete_action", fail)
    response = complete_credential_action(_payload(), MagicMock())

    assert response.status_code == 400
    assert "detalhe interno" not in response.body.decode()
    assert "Solicite um novo link" in response.body.decode()


def test_http_returns_actionable_password_policy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise PasswordPolicyError("A senha é muito comum.")

    monkeypatch.setattr("app.authentication.router.complete_action", fail)
    response = complete_credential_action(_payload(), MagicMock())

    assert response.status_code == 400
    assert "muito comum" in response.body.decode()
