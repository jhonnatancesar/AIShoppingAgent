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
    gemini_api_key_user: SecretStr | None = None
    gemini_api_key_admin_dev: SecretStr | None = None
    gemini_model: str = Field(default="gemini-3.6-flash", min_length=1)
    gemini_premium_model: str = Field(default="gemini-3.1-pro-preview", min_length=1)
    groq_api_key: SecretStr | None = None
    groq_model: str = Field(default="llama-3.3-70b-versatile", min_length=1)
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_notification_poll_seconds: float = Field(default=5.0, gt=0, le=3600)
    telegram_notification_batch_size: int = Field(default=50, ge=1, le=1000)
    observability_enabled: bool = False
    otel_exporter_otlp_traces_endpoint: str = Field(
        default="http://localhost:4318/v1/traces", min_length=1
    )
    trace_sample_ratio: float = Field(default=1.0, ge=0, le=1)
    readiness_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    worker_metrics_port: int = Field(default=9464, ge=1, le=65535)
    auth_public_base_url: str = Field(default="http://localhost:8000", min_length=1)


@lru_cache
def get_settings() -> Settings:
    """Retorna uma instância reutilizável das configurações validadas."""

    return Settings()
