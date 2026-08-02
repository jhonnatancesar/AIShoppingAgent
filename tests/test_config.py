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
