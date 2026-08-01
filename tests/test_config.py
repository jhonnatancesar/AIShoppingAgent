"""Testes da configuração tipada da aplicação."""

import pytest
from app.core.config import Settings
from pydantic import ValidationError


def test_settings_use_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configuração padrão deve iniciar em desenvolvimento sem debug."""
    monkeypatch.delenv("AISHOPPING_APP_NAME", raising=False)
    monkeypatch.delenv("AISHOPPING_ENVIRONMENT", raising=False)
    monkeypatch.delenv("AISHOPPING_DEBUG", raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "AIShoppingAgent"
    assert settings.environment == "development"
    assert settings.debug is False


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Variáveis com o prefixo do projeto devem sobrescrever os padrões."""
    monkeypatch.setenv("AISHOPPING_APP_NAME", "TestShoppingAgent")
    monkeypatch.setenv("AISHOPPING_ENVIRONMENT", "test")
    monkeypatch.setenv("AISHOPPING_DEBUG", "true")

    settings = Settings(_env_file=None)

    assert settings.app_name == "TestShoppingAgent"
    assert settings.environment == "test"
    assert settings.debug is True


def test_settings_reject_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambientes fora da lista permitida devem falhar na validação."""
    monkeypatch.setenv("AISHOPPING_ENVIRONMENT", "invalid")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
