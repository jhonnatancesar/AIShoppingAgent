"""Testes da infraestrutura compartilhada de banco de dados."""

import asyncio

import pytest
from app.core.config import Settings
from app.database.base import Base
from app.database.session import (
    DatabaseConfigurationError,
    _collection_connect_options,
    build_database_url,
    create_async_database_engine,
    create_async_session_factory,
    create_database_engine,
    create_session_factory,
)
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine


def test_database_url_requires_password() -> None:
    """A conexão não deve assumir uma senha insegura por padrão."""
    settings = Settings(database_password=None, _env_file=None)

    with pytest.raises(DatabaseConfigurationError):
        build_database_url(settings)


def test_database_url_preserves_special_password_and_hides_it() -> None:
    """A URL deve codificar a senha sem expô-la em sua representação."""
    settings = Settings(
        database_host="database",
        database_port=5432,
        database_name="shopping",
        database_user="agent",
        database_password="p@ss:%word",
        _env_file=None,
    )

    url = build_database_url(settings)

    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss:%word"
    assert url.host == "database"
    assert url.database == "shopping"
    assert "p@ss" not in str(url)
    assert "p%40ss%3A%25word" in url.render_as_string(hide_password=False)


def test_engine_and_session_factory_are_built_without_connecting() -> None:
    """A infraestrutura deve ser criada sem conexão antecipada ao PostgreSQL."""
    settings = Settings(database_password="local-password", _env_file=None)

    engine = create_database_engine(settings)
    factory = create_session_factory(engine)

    assert isinstance(engine, Engine)
    assert factory.kw["autoflush"] is False
    assert factory.kw["expire_on_commit"] is False
    engine.dispose()


def test_async_engine_and_session_factory_are_built_without_connecting() -> None:
    """TASK-079: engine assíncrono dedicado ao caminho do `collection_worker`
    -- não deve conectar antecipadamente, e deve aplicar os airbags de
    timeout (`lock_timeout`/`statement_timeout`/
    `idle_in_transaction_session_timeout`) só nesta conexão."""
    settings = Settings(database_password="local-password", _env_file=None)

    engine = create_async_database_engine(settings)
    factory = create_async_session_factory(engine)

    assert isinstance(engine, AsyncEngine)
    assert factory.kw["autoflush"] is False
    assert factory.kw["expire_on_commit"] is False
    asyncio.run(engine.dispose())


def test_async_engine_connect_options_apply_configured_timeouts() -> None:
    """TASK-079: os airbags de timeout só valem para a conexão dedicada do
    `collection_worker` (via `options` libpq), nunca `postgresql.conf`
    global."""
    settings = Settings(
        database_password="local-password",
        collection_lock_timeout_seconds=5.0,
        collection_statement_timeout_seconds=7.5,
        collection_idle_in_transaction_timeout_seconds=3.0,
        _env_file=None,
    )

    options = _collection_connect_options(settings)

    assert "lock_timeout=5000" in options
    assert "statement_timeout=7500" in options
    assert "idle_in_transaction_session_timeout=3000" in options


def test_async_engine_uses_default_timeout_settings() -> None:
    settings = Settings(database_password="local-password", _env_file=None)

    options = _collection_connect_options(settings)

    assert "lock_timeout=10000" in options
    assert "statement_timeout=15000" in options
    assert "idle_in_transaction_session_timeout=10000" in options


def test_metadata_contains_only_implemented_tables() -> None:
    """A metadata não deve antecipar tabelas de tarefas futuras."""
    assert set(Base.metadata.tables) == {
        "users",
        "products",
        "stores",
        "offers",
        "audit_entries",
        "missions",
        "mission_criteria",
        "mission_transitions",
        "sellers",
        "mission_sources",
        "mission_schedules",
        "collection_runs",
        "price_observations",
        "events",
        "event_consumption_attempts",
        "purchase_confirmations",
        "purchase_trail_entries",
        "user_credentials",
        "user_auth_sessions",
        "credential_action_tokens",
        "telegram_update_receipts",
        "mission_offer_relevance",
    }
