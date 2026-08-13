"""Testes do advisory lock de serialização por usuário do webhook Telegram.

Extensão da TASK-079 (ver docs/tasks/TASK-079.md): garante que a chave --
o `telegram_user_id` numérico do `Update`, disponível antes de qualquer
consulta ao banco -- é validada contra o intervalo do `bigint` do Postgres,
e que o polling de `pg_try_advisory_lock` adquire, executa o corpo
protegido e libera o lock mesmo quando o corpo levanta uma exceção."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.telegram.concurrency import advisory_lock_key, user_serialization_lock

_SAMPLE_TELEGRAM_USER_ID = 123_456_789


def test_advisory_lock_key_accepts_typical_telegram_ids() -> None:
    for candidate in (1, _SAMPLE_TELEGRAM_USER_ID, 2**63 - 1):
        assert advisory_lock_key(candidate) == candidate


@pytest.mark.parametrize("invalid", [0, -1, 2**63])
def test_advisory_lock_key_rejects_values_outside_bigint_range(invalid: int) -> None:
    with pytest.raises(ValueError):
        advisory_lock_key(invalid)


def _mock_engine(connection: AsyncMock) -> MagicMock:
    engine = MagicMock()

    async def _connect():
        return connection

    engine.connect = MagicMock(side_effect=_connect)
    return engine


def test_user_serialization_lock_acquires_runs_and_releases() -> None:
    connection = AsyncMock()
    connection.execution_options = AsyncMock(return_value=connection)
    connection.scalar = AsyncMock(return_value=True)
    engine = _mock_engine(connection)

    async def _run() -> bool:
        ran = False
        async with user_serialization_lock(engine, _SAMPLE_TELEGRAM_USER_ID):
            ran = True
        return ran

    assert asyncio.run(_run()) is True
    connection.execution_options.assert_awaited_once_with(isolation_level="AUTOCOMMIT")
    assert connection.scalar.await_count == 1
    assert connection.execute.await_count == 1
    connection.close.assert_awaited_once()


def test_user_serialization_lock_releases_even_if_body_raises() -> None:
    connection = AsyncMock()
    connection.execution_options = AsyncMock(return_value=connection)
    connection.scalar = AsyncMock(return_value=True)
    engine = _mock_engine(connection)

    async def _run() -> None:
        async with user_serialization_lock(engine, _SAMPLE_TELEGRAM_USER_ID):
            raise ValueError("boom")

    with pytest.raises(ValueError):
        asyncio.run(_run())

    connection.execute.assert_awaited_once()
    connection.close.assert_awaited_once()


def test_user_serialization_lock_polls_until_acquired(monkeypatch) -> None:
    connection = AsyncMock()
    connection.execution_options = AsyncMock(return_value=connection)
    connection.scalar = AsyncMock(side_effect=[False, False, True])
    engine = _mock_engine(connection)

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.telegram.concurrency.asyncio.sleep", _fake_sleep)

    async def _run() -> None:
        async with user_serialization_lock(engine, _SAMPLE_TELEGRAM_USER_ID):
            pass

    asyncio.run(_run())

    assert connection.scalar.await_count == 3
    assert sleeps == [0.02, 0.04]
    connection.close.assert_awaited_once()


def test_user_serialization_lock_always_closes_connection_on_setup_failure() -> None:
    connection = AsyncMock()
    connection.execution_options = AsyncMock(
        side_effect=RuntimeError("connection lost")
    )
    engine = _mock_engine(connection)

    async def _run() -> None:
        async with user_serialization_lock(engine, _SAMPLE_TELEGRAM_USER_ID):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(_run())

    connection.close.assert_awaited_once()
