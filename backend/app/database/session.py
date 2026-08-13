"""Construção explícita da conexão e das sessões do banco."""

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
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


def create_database_engine(
    settings: Settings | None = None,
    *,
    connect_timeout_seconds: float | None = None,
) -> Engine:
    """Cria o engine síncrono compartilhável pela aplicação."""
    current_settings = settings or get_settings()
    connect_args = {}
    if connect_timeout_seconds is not None:
        connect_args["connect_timeout"] = max(1, int(connect_timeout_seconds))
    engine = create_engine(
        build_database_url(current_settings),
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if current_settings.observability_enabled:
        from app.observability.tracing import instrument_sqlalchemy_engine

        instrument_sqlalchemy_engine(engine)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Cria uma fábrica sem estado global e com transações explícitas."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _collection_connect_options(settings: Settings) -> str:
    """Monta a string `options` (libpq) com os airbags de timeout do
    `collection_worker` (TASK-079) -- extraído para ser testável sem
    inspecionar internals do engine/pool do SQLAlchemy."""
    return (
        f"-c lock_timeout={int(settings.collection_lock_timeout_seconds * 1000)} "
        f"-c statement_timeout={int(settings.collection_statement_timeout_seconds * 1000)} "
        "-c idle_in_transaction_session_timeout="
        f"{int(settings.collection_idle_in_transaction_timeout_seconds * 1000)}"
    )


def create_async_database_engine(
    settings: Settings | None = None,
    *,
    connect_timeout_seconds: float | None = None,
) -> AsyncEngine:
    """Engine assíncrono dedicado ao caminho do `collection_worker` (TASK-079).

    Não substitui `create_database_engine`: API, Telegram e demais
    serviços continuam na `Session` síncrona. Este engine existe só para
    que o caminho de orquestração da coleta nunca execute I/O bloqueante
    do Postgres direto na thread do event loop -- causa raiz comprovada do
    autodeadlock (ver TASK-079).

    Os timeouts de `lock_timeout`/`statement_timeout`/
    `idle_in_transaction_session_timeout` são aplicados via `options` da
    conexão (libpq), portanto só valem para conexões abertas por este
    engine -- nunca alteram `postgresql.conf` nem afetam outros serviços.
    São um airbag, não a correção: a correção é nunca manter uma dessas
    transações aberta durante um `await` externo.
    """
    current_settings = settings or get_settings()
    connect_args: dict[str, object] = {
        "options": _collection_connect_options(current_settings)
    }
    if connect_timeout_seconds is not None:
        connect_args["connect_timeout"] = max(1, int(connect_timeout_seconds))
    engine = create_async_engine(
        build_database_url(current_settings),
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if current_settings.observability_enabled:
        from app.observability.tracing import instrument_sqlalchemy_engine

        instrument_sqlalchemy_engine(engine.sync_engine)
    return engine


def create_async_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Cria uma fábrica de `AsyncSession` sem estado global (TASK-079).

    Cada claim/task concorrente do `collection_worker` deve pedir sua
    própria `AsyncSession` a esta fábrica -- nunca compartilhar uma sessão
    entre tasks do `asyncio.gather`.
    """
    return async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
