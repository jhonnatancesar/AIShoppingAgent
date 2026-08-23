"""Testes da configuração tipada da aplicação."""

import pytest
from app.core.config import Settings
from pydantic import ValidationError


def test_settings_use_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configuração padrão deve iniciar em desenvolvimento sem debug."""
    monkeypatch.delenv("AISHOPPING_APP_NAME", raising=False)
    monkeypatch.delenv("AISHOPPING_ENVIRONMENT", raising=False)
    monkeypatch.delenv("AISHOPPING_DEBUG", raising=False)
    monkeypatch.delenv("AISHOPPING_LOG_LEVEL", raising=False)
    monkeypatch.delenv("AISHOPPING_DATABASE_HOST", raising=False)
    monkeypatch.delenv("AISHOPPING_DATABASE_PORT", raising=False)
    monkeypatch.delenv("AISHOPPING_DATABASE_NAME", raising=False)
    monkeypatch.delenv("AISHOPPING_DATABASE_USER", raising=False)
    monkeypatch.delenv("AISHOPPING_DATABASE_PASSWORD", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "AIShoppingAgent"
    assert settings.environment == "development"
    assert settings.debug is False
    assert settings.log_level == "INFO"
    assert settings.database_host == "localhost"
    assert settings.database_port == 5432
    assert settings.database_password is None
    assert settings.observability_enabled is False
    assert settings.trace_sample_ratio == 1.0
    assert settings.readiness_timeout_seconds == 1.0
    assert settings.worker_metrics_port == 9464
    assert settings.edge_cdp_url is None


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Variáveis com o prefixo do projeto devem sobrescrever os padrões."""
    monkeypatch.setenv("AISHOPPING_APP_NAME", "TestShoppingAgent")
    monkeypatch.setenv("AISHOPPING_ENVIRONMENT", "test")
    monkeypatch.setenv("AISHOPPING_DEBUG", "true")
    monkeypatch.setenv("AISHOPPING_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AISHOPPING_DATABASE_HOST", "database")
    monkeypatch.setenv("AISHOPPING_DATABASE_PORT", "55432")
    monkeypatch.setenv("AISHOPPING_DATABASE_PASSWORD", "test-password")

    settings = Settings(_env_file=None)

    assert settings.app_name == "TestShoppingAgent"
    assert settings.environment == "test"
    assert settings.debug is True
    assert settings.log_level == "DEBUG"
    assert settings.database_host == "database"
    assert settings.database_port == 55432
    assert settings.database_password is not None
    assert settings.database_password.get_secret_value() == "test-password"


def test_settings_reject_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambientes fora da lista permitida devem falhar na validação."""
    monkeypatch.setenv("AISHOPPING_ENVIRONMENT", "invalid")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_reject_invalid_database_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Portas fora do intervalo TCP devem falhar na validação."""
    monkeypatch.setenv("AISHOPPING_DATABASE_PORT", "70000")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://0.0.0.0:9223",
        "http://192.168.1.10:9223",
        "https://127.0.0.1:9223",
        "http://127.0.0.1",
        "http://user:pass@127.0.0.1:9223",
    ),
)
def test_settings_reject_non_loopback_or_unsafe_edge_cdp(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="loopback HTTP"):
        Settings(edge_cdp_url=endpoint, _env_file=None)


def test_settings_accept_loopback_edge_cdp() -> None:
    settings = Settings(edge_cdp_url="http://localhost:9223/", _env_file=None)

    assert settings.edge_cdp_url == "http://localhost:9223"


def test_settings_accept_legacy_magalu_cdp_env_var_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TASK-105: `edge_cdp_url` era `magalu_cdp_url` -- o nome antigo
    continua funcionando, sem exigir reconfiguração de ambiente já
    existente."""
    monkeypatch.setenv("AISHOPPING_MAGALU_CDP_URL", "http://127.0.0.1:9223")

    settings = Settings(_env_file=None)

    assert settings.edge_cdp_url == "http://127.0.0.1:9223"


def test_settings_prefer_new_edge_cdp_env_var_over_legacy_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AISHOPPING_EDGE_CDP_URL", "http://127.0.0.1:9224")
    monkeypatch.setenv("AISHOPPING_MAGALU_CDP_URL", "http://127.0.0.1:9223")

    settings = Settings(_env_file=None)

    assert settings.edge_cdp_url == "http://127.0.0.1:9224"


def test_settings_load_secret_from_file(tmp_path) -> None:
    secret_file = tmp_path / "database_password"
    secret_file.write_text("file-secret-with-spaces  \n", encoding="utf-8")

    settings = Settings(
        database_password_file=secret_file,
        _env_file=None,
    )

    assert settings.database_password is not None
    assert settings.database_password.get_secret_value() == "file-secret-with-spaces  "


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_settings_reject_empty_secret_file(tmp_path, value: str) -> None:
    secret_file = tmp_path / "database_password"
    secret_file.write_text(value, encoding="utf-8")

    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(database_password_file=secret_file, _env_file=None)


def test_settings_reject_multiline_secret_file(tmp_path) -> None:
    secret_file = tmp_path / "database_password"
    secret_file.write_text("first\nsecond\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="exactly one line"):
        Settings(database_password_file=secret_file, _env_file=None)


def test_settings_reject_missing_secret_file(tmp_path) -> None:
    with pytest.raises(ValidationError, match="regular file"):
        Settings(
            database_password_file=tmp_path / "missing",
            _env_file=None,
        )


def test_settings_reject_direct_and_file_secret_in_every_environment(tmp_path) -> None:
    secret_file = tmp_path / "database_password"
    secret_file.write_text("file-secret", encoding="utf-8")

    with pytest.raises(ValidationError, match="cannot be configured together"):
        Settings(
            database_password="direct-secret",
            database_password_file=secret_file,
            _env_file=None,
        )


def test_settings_allow_direct_secret_in_development() -> None:
    settings = Settings(database_password="development-secret", _env_file=None)

    assert settings.database_password is not None
    assert settings.database_password.get_secret_value() == "development-secret"


def test_settings_reject_direct_secret_in_production() -> None:
    with pytest.raises(ValidationError, match="must be provided through"):
        Settings(
            environment="production",
            database_password="production-secret",
            _env_file=None,
        )


def test_settings_load_secret_file_in_production(tmp_path) -> None:
    secret_file = tmp_path / "database_password"
    secret_file.write_text("production-file-secret\n", encoding="utf-8")

    settings = Settings(
        environment="production",
        database_password_file=secret_file,
        _env_file=None,
    )

    assert settings.database_password is not None
    assert settings.database_password.get_secret_value() == "production-file-secret"


def test_settings_reject_empty_direct_secret() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(database_password="   ", _env_file=None)


def test_settings_secret_repr_is_redacted(tmp_path) -> None:
    secret_file = tmp_path / "telegram_bot_token"
    secret_file.write_text("telegram-secret-canary", encoding="utf-8")

    settings = Settings(telegram_bot_token_file=secret_file, _env_file=None)

    assert "telegram-secret-canary" not in repr(settings)
