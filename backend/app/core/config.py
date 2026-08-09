"""Configuração tipada da aplicação baseada em variáveis de ambiente."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIRECTORY = Path(__file__).resolve().parents[2]

_SECRET_FILE_FIELDS = {
    "database_password": "database_password_file",
    "gemini_api_key_user": "gemini_api_key_user_file",
    "gemini_api_key_admin_dev": "gemini_api_key_admin_dev_file",
    "groq_api_key": "groq_api_key_file",
    "telegram_bot_token": "telegram_bot_token_file",
    "telegram_webhook_secret": "telegram_webhook_secret_file",
}
_MAX_SECRET_FILE_BYTES = 16 * 1024


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
    database_password_file: Path | None = None
    gemini_api_key_user: SecretStr | None = None
    gemini_api_key_user_file: Path | None = None
    gemini_api_key_admin_dev: SecretStr | None = None
    gemini_api_key_admin_dev_file: Path | None = None
    gemini_model: str = Field(default="gemini-3.6-flash", min_length=1)
    gemini_premium_model: str = Field(default="gemini-3.1-pro-preview", min_length=1)
    groq_api_key: SecretStr | None = None
    groq_api_key_file: Path | None = None
    groq_model: str = Field(default="llama-3.3-70b-versatile", min_length=1)
    telegram_bot_token: SecretStr | None = None
    telegram_bot_token_file: Path | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_secret_file: Path | None = None
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

    @model_validator(mode="after")
    def resolve_secret_files(self) -> Settings:
        """Resolve uma única fonte por segredo e proíbe ENV direto em produção."""
        for secret_field, file_field in _SECRET_FILE_FIELDS.items():
            direct_value = getattr(self, secret_field)
            file_path = getattr(self, file_field)

            if direct_value is not None and not direct_value.get_secret_value().strip():
                raise ValueError(f"{secret_field} must not be empty")
            if direct_value is not None and file_path is not None:
                raise ValueError(
                    f"{secret_field} and {file_field} cannot be configured together"
                )
            if self.environment == "production" and direct_value is not None:
                raise ValueError(
                    f"{secret_field} must be provided through {file_field} in production"
                )
            if file_path is not None:
                setattr(
                    self,
                    secret_field,
                    SecretStr(_read_secret_file(file_path, file_field)),
                )
        return self


def _read_secret_file(path: Path, field_name: str) -> str:
    """Lê um secret pequeno sem normalizar espaços que façam parte do valor."""
    try:
        if not path.is_file():
            raise ValueError(f"{field_name} must reference a regular file")
        if path.stat().st_size > _MAX_SECRET_FILE_BYTES:
            raise ValueError(f"{field_name} exceeds the maximum allowed size")
        value = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"{field_name} could not be read") from error

    value = value.removesuffix("\n").removesuffix("\r")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must contain exactly one line")
    return value


@lru_cache
def get_settings() -> Settings:
    """Retorna uma instância reutilizável das configurações validadas."""

    return Settings()
