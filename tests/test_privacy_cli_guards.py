"""CLI de privacidade: confirmação forte e falhas sem mutação acidental.

O serviço é controlado; a suíte PostgreSQL cobre a operação real.
"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.privacy import cli
from app.privacy.service import HistoricalPersonalDataConflict, PrivacyIdentityMismatch


@pytest.fixture
def command(monkeypatch):
    user_id = uuid4()
    session = object()
    events = []

    @contextmanager
    def begin():
        try:
            yield session
        except BaseException:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    engine = SimpleNamespace(dispose=Mock())
    monkeypatch.setattr(cli, "Settings", lambda: object())
    monkeypatch.setattr(cli, "create_database_engine", lambda settings: engine)
    monkeypatch.setattr(
        cli, "create_session_factory", lambda engine: SimpleNamespace(begin=begin)
    )
    monkeypatch.setattr(
        "sys.argv",
        ["privacy", "deidentify-account", "--user-id", str(user_id), "--execute"],
    )
    monkeypatch.setattr(cli, "getpass", lambda prompt: "123456789")
    monkeypatch.setattr("builtins.input", lambda prompt: "DESIDENTIFICAR")
    action = Mock(
        return_value=SimpleNamespace(
            missions_scrubbed=2,
            credentials_deleted=1,
            action_tokens_deleted=1,
            auth_sessions_deleted=1,
            already_deidentified=False,
        )
    )
    monkeypatch.setattr(cli, "deidentify_account", action)
    return user_id, session, engine, action, events


def test_cli_deidentification_passes_exact_identity_and_commits(command, capsys):
    user_id, session, engine, action, events = command
    cli.main()
    action.assert_called_once_with(
        session, user_id=user_id, expected_telegram_user_id=123456789
    )
    assert events == ["commit"]
    engine.dispose.assert_called_once()
    output = capsys.readouterr().out
    assert "desidentificação concluída" in output
    assert "123456789" not in output and str(user_id) not in output


@pytest.mark.parametrize("failure", ["invalid_identity", "cancelled"])
def test_cli_invalid_identity_or_confirmation_never_mutates(
    command, monkeypatch, failure
):
    _, _, engine, action, events = command
    if failure == "invalid_identity":
        monkeypatch.setattr(cli, "getpass", lambda prompt: "não é id")
    else:
        monkeypatch.setattr("builtins.input", lambda prompt: "não")
    with pytest.raises(SystemExit):
        cli.main()
    action.assert_not_called()
    assert events == ["rollback"]
    engine.dispose.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        HistoricalPersonalDataConflict(("price_history",)),
        PrivacyIdentityMismatch("sensitive identity detail"),
    ],
)
def test_cli_conflicts_roll_back_and_do_not_print_sensitive_exception(
    command, capsys, error
):
    _, _, engine, action, events = command
    action.side_effect = error
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert "operação recusada" in str(caught.value)
    assert "sensitive identity detail" not in str(caught.value)
    assert "concluída" not in capsys.readouterr().out
    assert events == ["rollback"]
    engine.dispose.assert_called_once()


def test_cli_cleanup_requires_execute_and_reports_counts_only(
    command, monkeypatch, capsys
):
    _, session, engine, _, events = command
    cleanup = Mock(
        return_value=SimpleNamespace(action_tokens_deleted=3, auth_sessions_deleted=2)
    )
    monkeypatch.setattr(cli, "cleanup_expired_authentication_artifacts", cleanup)
    monkeypatch.setattr("sys.argv", ["privacy", "cleanup-auth"])
    with pytest.raises(SystemExit):
        cli.main()
    cleanup.assert_not_called()
    assert events == []
    monkeypatch.setattr("sys.argv", ["privacy", "cleanup-auth", "--execute"])
    cli.main()
    assert cleanup.call_args.args == (session,)
    assert cleanup.call_args.kwargs["now"].tzinfo is not None
    assert events == ["commit"]
    assert "tokens=3, sessions=2" in capsys.readouterr().out
    engine.dispose.assert_called_once()
