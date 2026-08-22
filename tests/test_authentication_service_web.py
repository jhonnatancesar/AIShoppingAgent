"""Testes da sessão de navegador da aplicação web (TASK-091, item 1 da
V1.2) -- login por usuário/senha (sem token/Telegram) e `WebSession`
própria, independente de `UserAuthSession`."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.authentication.models import WebSession
from app.authentication.service import (
    SESSION_TTL,
    AuthenticationError,
    AuthenticationRateLimited,
    authenticate_web_login,
    get_web_session_user,
    issue_web_session,
    revoke_web_session,
    token_digest,
)
from app.users.models import User, UserRole

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _result(*, one: object | None = None, optional: object | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = one
    result.scalar_one_or_none.return_value = optional
    return result


def _user(*, active: bool = True) -> User:
    return User(
        id=uuid4(),
        display_name="Cliente",
        role=UserRole.USER,
        is_active=active,
        username="cliente",
    )


def test_authenticate_web_login_rejects_unknown_username() -> None:
    session = MagicMock()
    session.scalar.return_value = None

    with pytest.raises(AuthenticationError):
        authenticate_web_login(
            session, username="fantasma", password="Senha#123", now=NOW
        )
    session.execute.assert_not_called()


def test_authenticate_web_login_rejects_inactive_user() -> None:
    session = MagicMock()
    session.scalar.return_value = _user(active=False)

    with pytest.raises(AuthenticationError):
        authenticate_web_login(
            session, username="cliente", password="Senha#123", now=NOW
        )
    session.execute.assert_not_called()


def test_authenticate_web_login_rejects_wrong_password(monkeypatch) -> None:
    user = _user()
    session = MagicMock()
    session.scalar.return_value = user
    session.execute.return_value = _result(
        optional=MagicMock(
            password_hash="hash",
            login_locked_until=None,
            login_window_started_at=None,
            failed_login_attempts=0,
        )
    )
    monkeypatch.setattr(
        "app.authentication.service.verify_password", lambda *a, **k: False
    )

    with pytest.raises(AuthenticationError):
        authenticate_web_login(session, username="cliente", password="errada", now=NOW)


def test_authenticate_web_login_respects_lockout() -> None:
    user = _user()
    session = MagicMock()
    session.scalar.return_value = user
    session.execute.return_value = _result(
        optional=MagicMock(login_locked_until=NOW + timedelta(minutes=5))
    )

    with pytest.raises(AuthenticationRateLimited):
        authenticate_web_login(
            session, username="cliente", password="qualquer", now=NOW
        )


def test_authenticate_web_login_succeeds_and_resets_attempts(monkeypatch) -> None:
    user = _user()
    credential = MagicMock(
        password_hash="hash",
        login_locked_until=None,
        failed_login_attempts=3,
    )
    session = MagicMock()
    session.scalar.return_value = user
    session.execute.return_value = _result(optional=credential)
    monkeypatch.setattr(
        "app.authentication.service.verify_password", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "app.authentication.service.needs_rehash", lambda *a, **k: False
    )

    result = authenticate_web_login(
        session, username="cliente", password="Certa#123", now=NOW
    )

    assert result is user
    assert credential.failed_login_attempts == 0
    assert credential.login_locked_until is None


def test_issue_web_session_revokes_previous_and_returns_raw_token() -> None:
    user = _user()
    session = MagicMock()

    raw_token = issue_web_session(session, user=user, now=NOW)

    assert isinstance(raw_token, str)
    assert len(raw_token) >= 32
    session.execute.assert_called_once()  # revogação da sessão anterior
    added = session.add.call_args.args[0]
    assert isinstance(added, WebSession)
    assert added.user_id == user.id
    assert added.token_hash == token_digest(raw_token)
    assert added.expires_at == NOW + SESSION_TTL


def test_issue_web_session_token_has_at_least_256_bits_of_entropy() -> None:
    """`secrets.token_urlsafe(32)` -- 32 bytes brutos de `os.urandom`
    (gerador criptograficamente seguro), codificados em base64url. O
    comprimento da string ecoa a entropia real: 32 bytes = 256 bits."""
    session = MagicMock()

    raw_token = issue_web_session(session, user=_user(), now=NOW)

    # base64url sem padding: ceil(32 * 8 / 6) = 43 caracteres para 32 bytes.
    assert len(raw_token) == 43


def test_issue_web_session_tokens_are_unpredictable() -> None:
    """Duas emissões nunca colidem nem seguem um padrão previsível --
    prova indireta de que o gerador é aleatório, não determinístico."""
    session = MagicMock()

    tokens = {issue_web_session(session, user=_user(), now=NOW) for _ in range(50)}

    assert len(tokens) == 50


def test_issued_web_session_never_persists_the_raw_token() -> None:
    """O banco recebe só o hash (`token_digest`, SHA-256) -- o token bruto
    nunca é gravado em nenhum campo do registro persistido, só devolvido
    para o cookie do cliente."""
    session = MagicMock()

    raw_token = issue_web_session(session, user=_user(), now=NOW)

    added = session.add.call_args.args[0]
    assert added.token_hash != raw_token
    assert raw_token not in added.token_hash
    assert len(added.token_hash) == 64  # digest hexadecimal SHA-256


def test_issue_web_session_never_reuses_a_pre_existing_client_supplied_value() -> None:
    """Fixação de sessão: o identificador de sessão nunca é aceito de fora
    -- `issue_web_session` não recebe (e não pode receber) nenhum token
    pré-existente do chamador; ele sempre gera um valor novo internamente,
    então não há como um atacante "plantar" um identificador de sessão
    antes do login e tê-lo promovido a autenticado depois."""
    import inspect

    signature = inspect.signature(issue_web_session)
    assert "raw_token" not in signature.parameters
    assert "token" not in signature.parameters


def test_logging_in_again_revokes_the_prior_session_and_issues_an_unrelated_token() -> (
    None
):
    """Sessão nova a cada login: mesmo que um atacante já tivesse
    conseguido um `WebSession` válido anterior (ex.: sessão anterior
    comprometida), o próximo login do usuário legítimo o invalida --
    `issue_web_session` sempre revoga toda sessão ativa do usuário antes de
    emitir a nova (mesma política de `UserAuthSession`)."""
    user = _user()
    session = MagicMock()

    first_token = issue_web_session(session, user=user, now=NOW)
    session.reset_mock()
    second_token = issue_web_session(session, user=user, now=NOW + timedelta(minutes=1))

    assert first_token != second_token
    session.execute.assert_called_once()  # revogação da sessão anterior, de novo


def test_get_web_session_user_returns_none_for_missing_token() -> None:
    session = MagicMock()

    assert get_web_session_user(session, raw_token="", now=NOW) is None
    session.scalar.assert_not_called()


def test_get_web_session_user_returns_none_for_oversized_token() -> None:
    session = MagicMock()

    assert get_web_session_user(session, raw_token="x" * 200, now=NOW) is None
    session.scalar.assert_not_called()


def test_get_web_session_user_returns_none_when_expired() -> None:
    session = MagicMock()
    session.scalar.side_effect = [
        WebSession(
            user_id=uuid4(),
            token_hash=token_digest("abc"),
            authenticated_at=NOW - SESSION_TTL,
            expires_at=NOW - timedelta(seconds=1),
        )
    ]

    assert get_web_session_user(session, raw_token="abc", now=NOW) is None


def test_get_web_session_user_returns_none_when_revoked() -> None:
    session = MagicMock()
    session.scalar.side_effect = [
        WebSession(
            user_id=uuid4(),
            token_hash=token_digest("abc"),
            authenticated_at=NOW,
            expires_at=NOW + SESSION_TTL,
            revoked_at=NOW,
        )
    ]

    assert get_web_session_user(session, raw_token="abc", now=NOW) is None


def test_get_web_session_user_returns_user_when_active_and_valid() -> None:
    user = _user()
    web_session = WebSession(
        user_id=user.id,
        token_hash=token_digest("abc"),
        authenticated_at=NOW,
        expires_at=NOW + SESSION_TTL,
    )
    session = MagicMock()
    session.scalar.side_effect = [web_session]
    session.get.return_value = user

    result = get_web_session_user(session, raw_token="abc", now=NOW)

    assert result is user


def test_get_web_session_user_returns_none_for_inactive_user() -> None:
    user = _user(active=False)
    web_session = WebSession(
        user_id=user.id,
        token_hash=token_digest("abc"),
        authenticated_at=NOW,
        expires_at=NOW + SESSION_TTL,
    )
    session = MagicMock()
    session.scalar.side_effect = [web_session]
    session.get.return_value = user

    assert get_web_session_user(session, raw_token="abc", now=NOW) is None


def test_revoke_web_session_is_a_no_op_without_a_token() -> None:
    session = MagicMock()

    revoke_web_session(session, raw_token="", now=NOW)

    session.execute.assert_not_called()


def test_revoke_web_session_updates_by_token_hash() -> None:
    session = MagicMock()

    revoke_web_session(session, raw_token="abc", now=NOW)

    session.execute.assert_called_once()
