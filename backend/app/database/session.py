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


def build_database_url(
    settings: Settings | None = None, *, drivername: str = "postgresql+psycopg"
) -> URL:
    """Monta uma URL PostgreSQL sem interpolar ou registrar a senha."""
    current_settings = settings or get_settings()
    password = current_settings.database_password
    if password is None:
        raise DatabaseConfigurationError(
            "AISHOPPING_DATABASE_PASSWORD é obrigatório para acessar o banco."
        )

    return URL.create(
        drivername=drivername,
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


def _timeout_milliseconds(
    *,
    lock_timeout_seconds: float,
    statement_timeout_seconds: float,
    idle_in_transaction_timeout_seconds: float,
) -> tuple[int, int, int]:
    """Conversão única de segundos para milissegundos -- ponto central para
    não duplicar a regra entre o formato psycopg (`options`, libpq) e o
    formato asyncpg (`server_settings`, TASK-109)."""
    return (
        int(lock_timeout_seconds * 1000),
        int(statement_timeout_seconds * 1000),
        int(idle_in_transaction_timeout_seconds * 1000),
    )


def _connect_options(
    *,
    lock_timeout_seconds: float,
    statement_timeout_seconds: float,
    idle_in_transaction_timeout_seconds: float,
) -> str:
    """Monta a string `options` (libpq) com airbags de timeout -- extraído
    para ser testável sem inspecionar internals do engine/pool do
    SQLAlchemy. Genérico: cada engine assíncrono dedicado (collection_worker,
    API/Telegram, ...) passa seus próprios valores; nunca compartilhado nem
    aplicado a `postgresql.conf` global. Só para o driver `psycopg`."""
    lock_ms, statement_ms, idle_ms = _timeout_milliseconds(
        lock_timeout_seconds=lock_timeout_seconds,
        statement_timeout_seconds=statement_timeout_seconds,
        idle_in_transaction_timeout_seconds=idle_in_transaction_timeout_seconds,
    )
    return (
        f"-c lock_timeout={lock_ms} "
        f"-c statement_timeout={statement_ms} "
        f"-c idle_in_transaction_session_timeout={idle_ms}"
    )


def _server_settings(
    *,
    lock_timeout_seconds: float,
    statement_timeout_seconds: float,
    idle_in_transaction_timeout_seconds: float,
) -> dict[str, str]:
    """Equivalente a `_connect_options` para o driver `asyncpg` (TASK-109,
    worker nativo Windows -- ver `docs/tasks/TASK-109.md`): asyncpg não
    aceita a string `options` do libpq, usa `server_settings` (dict de
    GUCs); valores explícitos em `ms` para não depender de interpretação
    implícita de unidade."""
    lock_ms, statement_ms, idle_ms = _timeout_milliseconds(
        lock_timeout_seconds=lock_timeout_seconds,
        statement_timeout_seconds=statement_timeout_seconds,
        idle_in_transaction_timeout_seconds=idle_in_transaction_timeout_seconds,
    )
    return {
        "lock_timeout": f"{lock_ms}ms",
        "statement_timeout": f"{statement_ms}ms",
        "idle_in_transaction_session_timeout": f"{idle_ms}ms",
    }


def create_async_database_engine(
    settings: Settings | None = None,
    *,
    connect_timeout_seconds: float | None = None,
    lock_timeout_seconds: float | None = None,
    statement_timeout_seconds: float | None = None,
    idle_in_transaction_timeout_seconds: float | None = None,
    async_driver: str = "psycopg",
) -> AsyncEngine:
    """Engine assíncrono dedicado a um caminho específico da aplicação
    (`collection_worker` -- TASK-079 -- ou webhook Telegram/API -- extensão
    da TASK-079).

    Não substitui `create_database_engine`: chamadores síncronos (rotas
    HTTP não migradas, outros serviços) continuam na `Session` síncrona.
    Este engine existe só para que um caminho específico nunca execute I/O
    bloqueante do Postgres direto na thread do event loop -- causa raiz
    comprovada de autodeadlock, primeiro no `collection_worker`, depois no
    mesmo padrão no webhook Telegram.

    Os timeouts de `lock_timeout`/`statement_timeout`/
    `idle_in_transaction_session_timeout` são aplicados via `options`
    (libpq, driver `psycopg`) ou `server_settings` (driver `asyncpg`,
    TASK-109) só quando fornecidos -- portanto só valem para conexões
    abertas por ESTE engine, nunca alteram `postgresql.conf` nem afetam
    outros serviços. São um airbag, não a correção: a correção é nunca
    manter uma dessas transações aberta durante um `await` externo.

    `async_driver` (TASK-109): `psycopg` (padrão, Linux/Docker -- exige
    `SelectorEventLoop`, incompatível com o `ProactorEventLoop` que o
    Playwright precisa no worker nativo Windows) ou `asyncpg` (worker
    Windows -- ver `docs/tasks/TASK-109.md`, sem essa restrição de loop).
    Nunca usado pelos demais caminhos (API, Telegram, scripts, migrations),
    que continuam em `psycopg` sem qualquer mudança.
    """
    current_settings = settings or get_settings()
    connect_args: dict[str, object] = {}
    if (
        lock_timeout_seconds is not None
        or statement_timeout_seconds is not None
        or idle_in_transaction_timeout_seconds is not None
    ):
        if (
            lock_timeout_seconds is None
            or statement_timeout_seconds is None
            or idle_in_transaction_timeout_seconds is None
        ):
            raise ValueError(
                "os três timeouts (lock/statement/idle_in_transaction) devem "
                "ser fornecidos juntos, ou nenhum deles"
            )
        if async_driver == "asyncpg":
            connect_args["server_settings"] = _server_settings(
                lock_timeout_seconds=lock_timeout_seconds,
                statement_timeout_seconds=statement_timeout_seconds,
                idle_in_transaction_timeout_seconds=idle_in_transaction_timeout_seconds,
            )
        else:
            connect_args["options"] = _connect_options(
                lock_timeout_seconds=lock_timeout_seconds,
                statement_timeout_seconds=statement_timeout_seconds,
                idle_in_transaction_timeout_seconds=idle_in_transaction_timeout_seconds,
            )
    if connect_timeout_seconds is not None:
        if async_driver == "asyncpg":
            connect_args["timeout"] = connect_timeout_seconds
        else:
            connect_args["connect_timeout"] = max(1, int(connect_timeout_seconds))
    engine = create_async_engine(
        build_database_url(
            current_settings, drivername=f"postgresql+{async_driver}"
        ),
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if current_settings.observability_enabled:
        from app.observability.tracing import instrument_sqlalchemy_engine

        instrument_sqlalchemy_engine(engine.sync_engine)
    return engine


def create_collection_async_database_engine(
    settings: Settings | None = None,
    *,
    connect_timeout_seconds: float | None = None,
    async_driver: str = "psycopg",
) -> AsyncEngine:
    """Atalho para o engine assíncrono do `collection_worker` (TASK-079),
    lendo os timeouts já configurados em `Settings`.

    `async_driver` (TASK-109): o worker nativo Windows passa `asyncpg`
    (ver `run_worker` em `app/collection/worker.py`); Linux/Docker mantém
    o padrão `psycopg`, sem mudança."""
    current_settings = settings or get_settings()
    return create_async_database_engine(
        current_settings,
        connect_timeout_seconds=connect_timeout_seconds,
        lock_timeout_seconds=current_settings.collection_lock_timeout_seconds,
        statement_timeout_seconds=current_settings.collection_statement_timeout_seconds,
        idle_in_transaction_timeout_seconds=(
            current_settings.collection_idle_in_transaction_timeout_seconds
        ),
        async_driver=async_driver,
    )


def create_telegram_async_database_engine(
    settings: Settings | None = None,
    *,
    connect_timeout_seconds: float | None = None,
) -> AsyncEngine:
    """Atalho para o engine assíncrono do webhook Telegram/API (extensão da
    TASK-079), lendo os timeouts já configurados em `Settings`."""
    current_settings = settings or get_settings()
    return create_async_database_engine(
        current_settings,
        connect_timeout_seconds=connect_timeout_seconds,
        lock_timeout_seconds=current_settings.telegram_lock_timeout_seconds,
        statement_timeout_seconds=current_settings.telegram_statement_timeout_seconds,
        idle_in_transaction_timeout_seconds=(
            current_settings.telegram_idle_in_transaction_timeout_seconds
        ),
    )


def create_async_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Cria uma fábrica de `AsyncSession` sem estado global (TASK-079).

    Cada claim/task concorrente do `collection_worker` deve pedir sua
    própria `AsyncSession` a esta fábrica -- nunca compartilhar uma sessão
    entre tasks do `asyncio.gather`.
    """
    return async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
