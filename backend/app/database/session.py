"""Construção explícita da conexão e das sessões do banco."""

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


class DatabaseConfigurationError(RuntimeError):
    """Indica ausência de configuração obrigatória para acessar o banco."""


def build_database_url(settings: Settings | None = None) -> URL:
    """Monta uma URL PostgreSQL sem interpolar ou registrar a senha."""
    current_settings = settings or get_settings()
    password = current_settings.database_password
    if password is None:
        raise DatabaseConfigurationError(
            "AISHOPPING_DATABASE_PASSWORD é obrigatório para acessar o banco."
        )

    return URL.create(
        drivername="postgresql+psycopg",
        username=current_settings.database_user,
        password=password.get_secret_value(),
        host=current_settings.database_host,
        port=current_settings.database_port,
        database=current_settings.database_name,
    )


def create_database_engine(settings: Settings | None = None) -> Engine:
    """Cria o engine síncrono compartilhável pela aplicação."""
    return create_engine(build_database_url(settings), pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Cria uma fábrica sem estado global e com transações explícitas."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
