"""Configuração tipada da aplicação baseada em variáveis de ambiente."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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


@lru_cache
def get_settings() -> Settings:
    """Retorna uma instância reutilizável das configurações validadas."""

    return Settings()
