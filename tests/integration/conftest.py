"""Fixtures fail-closed para bancos PostgreSQL clonados por teste."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from uuid import uuid4

import psycopg
import pytest
from app.core.config import Settings, get_settings
from app.database.session import (
    create_async_session_factory,
    create_collection_async_database_engine,
    create_database_engine,
    create_session_factory,
)
from psycopg import sql
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

if sys.platform == "win32":
    # Psycopg assíncrono não suporta o ProactorEventLoop padrão do Windows.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if os.getenv("AISHOPPING_INTEGRATION_RUN_ID"):
    Settings.model_config["env_file"] = None


@dataclass(frozen=True, slots=True)
class IntegrationDatabase:
    name: str
    expected_head: str
    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    # TASK-079: mesmo banco clonado, engine assíncrono dedicado -- usado
    # pelos testes de `CollectionOrchestrator` (caminho async real do
    # collection_worker, sem Session síncrona bloqueante).
    async_engine: AsyncEngine
    async_sessions: async_sessionmaker[AsyncSession]


def _required_environment() -> dict[str, str]:
    names = (
        "AISHOPPING_INTEGRATION_RUN_ID",
        "AISHOPPING_INTEGRATION_TEMPLATE_DATABASE",
        "AISHOPPING_INTEGRATION_GUARD_TOKEN",
        "AISHOPPING_INTEGRATION_EXPECTED_HEAD",
        "AISHOPPING_DATABASE_HOST",
        "AISHOPPING_DATABASE_PORT",
        "AISHOPPING_DATABASE_USER",
        "AISHOPPING_DATABASE_PASSWORD",
    )
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise pytest.UsageError(
            "integration tests must run through scripts/run_integration_tests.py; "
            "guard missing"
        )
    if values["AISHOPPING_DATABASE_HOST"] != "127.0.0.1":
        raise pytest.UsageError("integration database must use 127.0.0.1")
    run_id = values["AISHOPPING_INTEGRATION_RUN_ID"]
    if values["AISHOPPING_INTEGRATION_TEMPLATE_DATABASE"] != (
        f"aishopping_template_{run_id}"
    ):
        raise pytest.UsageError("integration template identity mismatch")
    return values


def _admin_connection(values: dict[str, str], *, database: str):
    return psycopg.connect(
        host="127.0.0.1",
        port=int(values["AISHOPPING_DATABASE_PORT"]),
        dbname=database,
        user=values["AISHOPPING_DATABASE_USER"],
        password=values["AISHOPPING_DATABASE_PASSWORD"],
        autocommit=True,
        connect_timeout=3,
    )


@pytest.fixture
def integration_database(
    monkeypatch: pytest.MonkeyPatch,
) -> IntegrationDatabase:
    values = _required_environment()
    run_id = values["AISHOPPING_INTEGRATION_RUN_ID"]
    database_name = f"aishopping_it_{run_id}_{uuid4().hex[:10]}"
    template_name = values["AISHOPPING_INTEGRATION_TEMPLATE_DATABASE"]

    with _admin_connection(values, database="postgres") as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {} WITH TEMPLATE {} OWNER {}").format(
                sql.Identifier(database_name),
                sql.Identifier(template_name),
                sql.Identifier(values["AISHOPPING_DATABASE_USER"]),
            )
        )

    monkeypatch.setenv("AISHOPPING_ENVIRONMENT", "test")
    monkeypatch.setenv("AISHOPPING_DATABASE_NAME", database_name)
    for file_variable in (
        "AISHOPPING_DATABASE_PASSWORD_FILE",
        "AISHOPPING_GEMINI_API_KEY_USER_FILE",
        "AISHOPPING_GEMINI_API_KEY_ADMIN_DEV_FILE",
        "AISHOPPING_GROQ_API_KEY_FILE",
        "AISHOPPING_TELEGRAM_BOT_TOKEN_FILE",
        "AISHOPPING_TELEGRAM_WEBHOOK_SECRET_FILE",
    ):
        monkeypatch.delenv(file_variable, raising=False)
    get_settings.cache_clear()
    settings = Settings(_env_file=None)
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    async_engine = create_collection_async_database_engine(settings)
    async_sessions = create_async_session_factory(async_engine)

    try:
        with engine.connect() as connection:
            guard = connection.execute(
                text(
                    "SELECT run_id, guard_token, expected_head "
                    "FROM integration_test_guard"
                )
            ).one()
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        expected = (
            run_id,
            values["AISHOPPING_INTEGRATION_GUARD_TOKEN"],
            values["AISHOPPING_INTEGRATION_EXPECTED_HEAD"],
        )
        if tuple(guard) != expected or revision != expected[2]:
            raise pytest.UsageError("integration database guard or head mismatch")
        yield IntegrationDatabase(
            name=database_name,
            expected_head=expected[2],
            settings=settings,
            engine=engine,
            sessions=sessions,
            async_engine=async_engine,
            async_sessions=async_sessions,
        )
    finally:
        asyncio.run(async_engine.dispose())
        engine.dispose()
        get_settings.cache_clear()
        with _admin_connection(values, database="postgres") as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(database_name)
                )
            )
