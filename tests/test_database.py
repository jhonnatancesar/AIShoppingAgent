"""Testes da infraestrutura compartilhada de banco de dados."""

import pytest
from app.core.config import Settings
from app.database.base import Base
from app.database.session import (
    DatabaseConfigurationError,
    build_database_url,
    create_database_engine,
    create_session_factory,
)
from sqlalchemy import Engine


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
    }
