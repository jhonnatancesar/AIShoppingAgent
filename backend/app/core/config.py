"""Configuração tipada da aplicação baseada em variáveis de ambiente."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Valores de configuração carregados do ambiente local ou do processo."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIRECTORY / ".env",
        env_file_encoding="utf-8",
        env_prefix="AISHOPPING_",
        extra="ignore",
    )

    app_name: str = "AIShoppingAgent"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_host: str = Field(default="localhost", min_length=1)
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(default="aishoppingagent", min_length=1)
    database_user: str = Field(default="aishoppingagent", min_length=1)
    database_password: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str = Field(default="gemini-3.6-flash", min_length=1)
    gemini_premium_model: str = Field(default="gemini-3.1-pro-preview", min_length=1)
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Retorna uma instância reutilizável das configurações validadas."""

    return Settings()
